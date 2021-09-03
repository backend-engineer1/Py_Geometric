import copy
import pytest
from itertools import product

import torch
from torch_geometric.nn import Linear
from torch.nn import Linear as PTLinear
from torch.nn.parameter import UninitializedParameter

weight_inits = ['glorot', 'kaiming_uniform', None]
bias_inits = ['zeros', None]


@pytest.mark.parametrize('weight,bias', product(weight_inits, bias_inits))
def test_linear(weight, bias):
    x = torch.randn(3, 4, 16)
    lin = Linear(16, 32, weight_initializer=weight, bias_initializer=bias)
    assert str(lin) == 'Linear(16, 32, bias=True)'
    assert lin(x).size() == (3, 4, 32)


@pytest.mark.parametrize('weight,bias', product(weight_inits, bias_inits))
def test_lazy_linear(weight, bias):
    x = torch.randn(3, 4, 16)
    lin = Linear(-1, 32, weight_initializer=weight, bias_initializer=bias)
    assert str(lin) == 'Linear(-1, 32, bias=True)'
    assert lin(x).size() == (3, 4, 32)
    assert str(lin) == 'Linear(16, 32, bias=True)'


@pytest.mark.parametrize('lazy', [True, False])
def test_identical_linear_default_initialization(lazy):
    x = torch.randn(3, 4, 16)

    torch.manual_seed(12345)
    lin1 = Linear(-1 if lazy else 16, 32)
    lin1(x)

    torch.manual_seed(12345)
    lin2 = PTLinear(16, 32)

    assert lin1.weight.tolist() == lin2.weight.tolist()
    assert lin1.bias.tolist() == lin2.bias.tolist()
    assert lin1(x).tolist() == lin2(x).tolist()


def test_copy_unintialized_parameter():
    weight = UninitializedParameter()
    with pytest.raises(Exception):
        copy.deepcopy(weight)


@pytest.mark.parametrize('lazy', [True, False])
def test_copy_linear(lazy):
    lin = Linear(-1 if lazy else 16, 32)

    copied_lin = copy.copy(lin)
    assert id(copied_lin) != id(lin)
    assert id(copied_lin.weight) == id(lin.weight)
    if not isinstance(copied_lin.weight, UninitializedParameter):
        assert copied_lin.weight.data_ptr() == lin.weight.data_ptr()
    assert id(copied_lin.bias) == id(lin.bias)
    assert copied_lin.bias.data_ptr() == lin.bias.data_ptr()

    copied_lin = copy.deepcopy(lin)
    assert id(copied_lin) != id(lin)
    assert id(copied_lin.weight) != id(lin.weight)
    if not isinstance(copied_lin.weight, UninitializedParameter):
        assert copied_lin.weight.data_ptr() != lin.weight.data_ptr()
        assert torch.allclose(copied_lin.weight, lin.weight)
    assert id(copied_lin.bias) != id(lin.bias)
    assert copied_lin.bias.data_ptr() != lin.bias.data_ptr()
    assert torch.allclose(copied_lin.bias, lin.bias)
