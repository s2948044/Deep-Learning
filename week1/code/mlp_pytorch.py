"""
This module implements a multi-layer perceptron (MLP) in PyTorch.
You should fill in code into indicated sections.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import torch.nn as nn

class MLP(nn.Module):
  """
  This class implements a Multi-layer Perceptron in PyTorch.
  It handles the different layers and parameters of the model.
  Once initialized an MLP object can perform forward.
  """

  def __init__(self, n_inputs, n_hidden, n_classes):
    """
    Initializes MLP object. 
    
    Args:
      n_inputs: number of inputs.
      n_hidden: list of ints, specifies the number of units
                in each linear layer. If the list is empty, the MLP
                will not have any linear layers, and the model
                will simply perform a multinomial logistic regression.
      n_classes: number of classes of the classification problem.
                 This number is required in order to specify the
                 output dimensions of the MLP
    
    TODO:
    Implement initialization of the network.
    """

    ########################
    # PUT YOUR CODE HERE  #
    #######################
    super(MLP, self).__init__()
    self.layers = nn.ModuleList()
    if len(n_hidden) == 0:
      self.layers.append(nn.Linear(n_inputs, n_classes, bias=True))
    else:
      for i in range(len(n_hidden)):
        if i == 0:
          self.layers.append(nn.Linear(n_inputs, n_hidden[i], bias=True))
          self.layers.append(nn.ReLU())
        else:
          self.layers.append(nn.Linear(n_hidden[i-1], n_hidden[i], bias=True))
          self.layers.append(nn.ReLU())
      self.layers.append(nn.Linear(n_hidden[-1], n_classes, bias=True))
    ########################
    # END OF YOUR CODE    #
    #######################

  def forward(self, x):
    """
    Performs forward pass of the input. Here an input tensor x is transformed through 
    several layer transformations.
    
    Args:
      x: input to the network
    Returns:
      out: outputs of the network
    
    TODO:
    Implement forward pass of the network.
    """

    ########################
    # PUT YOUR CODE HERE  #
    #######################
    for layer in self.layers:
      x = layer(x)
    out = x
    ########################
    # END OF YOUR CODE    #
    #######################

    return out
