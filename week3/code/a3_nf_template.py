import argparse
import os

import torch
import torch.nn as nn
import matplotlib.pyplot as plt
import torch.nn.functional as F
import numpy as np
from datasets.mnist import mnist
from torchvision.utils import make_grid
from torchvision.utils import save_image


def log_prior(x):
    """
    Compute the elementwise log probability of a standard Gaussian, i.e.
    N(x | mu=0, sigma=1).
    """
    logp = (-0.5 * np.log(2*np.pi) - 0.5 * (x * x)).sum(dim=-1)
    
    return logp


def sample_prior(size):
    """
    Sample from a standard Gaussian.
    """
    sample = torch.randn(size)

    if torch.cuda.is_available():
        sample = sample.cuda()

    return sample


def get_mask():
    mask = np.zeros((28, 28), dtype='float32')
    for i in range(28):
        for j in range(28):
            if (i + j) % 2 == 0:
                mask[i, j] = 1

    mask = mask.reshape(1, 28*28)
    mask = torch.from_numpy(mask)

    return mask


class Coupling(torch.nn.Module):
    def __init__(self, c_in, mask, n_hidden=1024, device='cuda:0'):
        super().__init__()
        self.n_hidden = n_hidden
        self.c_in = c_in
        self.to(device)

        # Assigns mask to self.mask and creates reference for pytorch.
        self.register_buffer('mask', mask)
        self.tanh = nn.Tanh()

        # Create shared architecture to generate both the translation and
        # scale variables.
        # Suggestion: Linear ReLU Linear ReLU Linear.
        self.nn = torch.nn.Sequential(
            nn.Linear(c_in, n_hidden),
            nn.ReLU(True),
            nn.Linear(n_hidden, n_hidden),
            nn.ReLU(True),
            nn.Linear(n_hidden, 2*c_in)
            )

        # The nn should be initialized such that the weights of the last layer
        # is zero, so that its initial transform is identity.
        self.nn[-1].weight.data.zero_()
        self.nn[-1].bias.data.zero_()

    def forward(self, z, ldj, reverse=False):
        # Implement the forward and inverse for an affine coupling layer. Split
        # the input using the mask in self.mask. Transform one part with
        # Make sure to account for the log Jacobian determinant (ldj).
        # For reference, check: Density estimation using RealNVP.

        # NOTE: For stability, it is advised to model the scale via:
        # log_scale = tanh(h), where h is the scale-output
        # from the NN.

        out = self.nn(self.mask*z)
        scale = out[:,:self.c_in]
        log_scale = self.tanh(scale)
        translate = out[:,self.c_in:]

        if not reverse:
            z =  self.mask * z + (1 - self.mask) * (z * torch.exp(log_scale) + translate)
            ldj += ((1 - self.mask) * log_scale).sum(dim=-1)
        else:
            z = self.mask * z + (1 - self.mask) * ((z - translate) * torch.exp(-log_scale))

        return z, ldj


class Flow(nn.Module):
    def __init__(self, shape, n_flows=4, device='cuda:0'):
        super().__init__()
        channels, = shape

        mask = get_mask()

        self.layers = torch.nn.ModuleList()

        for i in range(n_flows):
            self.layers.append(Coupling(c_in=channels, mask=mask, device=device))
            self.layers.append(Coupling(c_in=channels, mask=1-mask, device=device))

        self.z_shape = (channels,)
        self.to(device)

    def forward(self, z, logdet, reverse=False):
        if not reverse:
            for layer in self.layers:
                z, logdet = layer(z, logdet)
        else:
            for layer in reversed(self.layers):
                z, logdet = layer(z, logdet, reverse=True)

        return z, logdet


class Model(nn.Module):
    def __init__(self, shape, device='cuda:0'):
        super().__init__()
        self.flow = Flow(shape, device=device).to(device)
        self.to(device)

    def dequantize(self, z):
        return z + torch.rand_like(z)

    def logit_normalize(self, z, logdet, reverse=False):
        """
        Inverse sigmoid normalization.
        """
        alpha = 1e-5

        if not reverse:
            # Divide by 256 and update ldj.
            z = z / 256.
            logdet -= np.log(256) * np.prod(z.size()[1:])

            # Logit normalize
            z = z*(1-alpha) + alpha*0.5
            logdet += torch.sum(-torch.log(z) - torch.log(1-z), dim=1)
            z = torch.log(z) - torch.log(1-z)

        else:
            # Inverse normalize
            logdet += torch.sum(torch.log(z) + torch.log(1-z), dim=1)
            z = torch.sigmoid(z)

            # Multiply by 256.
            z = z * 256.
            logdet += np.log(256) * np.prod(z.size()[1:])

        return z, logdet

    def forward(self, input):
        """
        Given input, encode the input to z space. Also keep track of ldj.
        """
        z = input
        ldj = torch.zeros(z.size(0), device=z.device)

        z = self.dequantize(z)
        z, ldj = self.logit_normalize(z, ldj)

        z, ldj = self.flow(z, ldj)

        # Compute log_pz and log_px per example
        log_pz = log_prior(z)
        log_px = log_pz + ldj

        return log_px

    def sample(self, n_samples):
        """
        Sample n_samples from the model. Sample from prior and create ldj.
        Then invert the flow and invert the logit_normalize.
        """
        z = sample_prior((n_samples,) + self.flow.z_shape)
        ldj = torch.zeros(z.size(0), device=z.device)
        z, ldj = self.flow(z, ldj, reverse=True)
        z, _ = self.logit_normalize(z, ldj, reverse=True)

        return z


def epoch_iter(model, data, optimizer, device):
    """
    Perform a single epoch for either the training or validation.
    use model.training to determine if in 'training mode' or not.

    Returns the average bpd ("bits per dimension" which is the negative
    log_2 likelihood per dimension) averaged over the complete epoch.
    """
    avg_bpd = 0.
    if model.training:
        for i, (imgs, _) in enumerate(data):
            imgs = imgs.view(-1, 784).to(device)
            optimizer.zero_grad()
            logp = model(imgs)
            loss = -logp.mean(dim=0)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=ARGS.max_norm)
            optimizer.step()
            avg_bpd += loss.item()
    else:
        for i, (imgs, _) in enumerate(data):
            imgs = imgs.view(-1, 784).to(device)
            logp = model(imgs)
            loss = -logp.mean(dim=0)
            avg_bpd += loss.item()
    avg_bpd /= (len(data) * np.log(2) * 784)

    return avg_bpd


def run_epoch(model, data, optimizer, device):
    """
    Run a train and validation epoch and return average bpd for each.
    """
    traindata, valdata = data

    model.train()
    train_bpd = epoch_iter(model, traindata, optimizer, device)

    model.eval()
    val_bpd = epoch_iter(model, valdata, optimizer, device)

    return train_bpd, val_bpd


def save_bpd_plot(train_curve, val_curve, filename):
    plt.figure(figsize=(12, 6))
    plt.plot(train_curve, label='train bpd')
    plt.plot(val_curve, label='validation bpd')
    plt.legend()
    plt.xlabel('epochs')
    plt.ylabel('bpd')
    plt.tight_layout()
    plt.savefig(filename)


def main():
    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    # Print all configs to confirm parameter settings
    print_flags()
    data = mnist()[:2]  # ignore test split

    # Create output directories
    os.makedirs('./images/nf', exist_ok=True)

    model = Model(shape=[784], device=device).to(device)

    if torch.cuda.is_available():
        model = model.cuda()

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    train_curve, val_curve = [], []
    for epoch in range(ARGS.epochs):
        bpds = run_epoch(model, data, optimizer, device)
        train_bpd, val_bpd = bpds
        train_curve.append(train_bpd)
        val_curve.append(val_bpd)
        print("[Epoch {epoch}] train bpd: {train_bpd} val_bpd: {val_bpd}".format(
            epoch=epoch, train_bpd=train_bpd, val_bpd=val_bpd))

        # --------------------------------------------------------------------
        #  Add functionality to plot samples from model during training.
        #  You can use the make_grid functionality that is already imported.
        #  Save grid to images/nfs/
        # --------------------------------------------------------------------
        im_samples = model.sample(25)
        save_image(im_samples.view(im_samples.shape[0], 1, 28, 28),
                    './images/nf/{}.png'.format(epoch),
                    nrow=5, normalize=True)

    save_bpd_plot(train_curve, val_curve, './images/nf/nfs_bpd.png')
    save_bpd_plot(train_curve, val_curve, './images/nf/nfs_bpd.eps')

def print_flags():
  """
  Prints all entries in args variable.
  """
  for key, value in vars(ARGS).items():
    print(key + ' : ' + str(value))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--epochs', default=40, type=int,
                        help='max number of epochs')
    parser.add_argument('--max_norm', default=5, type=int,
                        help='max clipping norm')                    

    ARGS = parser.parse_args()

    main()
