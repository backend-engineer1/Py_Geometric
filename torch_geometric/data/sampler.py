from __future__ import division

import torch
from torch_cluster import neighbor_sampler
from torch_geometric.utils import degree
from torch_geometric.utils.repeat import repeat

from .data import size_repr


class Block(object):
    def __init__(self, n_id, e_id, edge_index, size):
        self.n_id = n_id
        self.e_id = e_id
        self.edge_index = edge_index
        self.size = size

    def __repr__(self):
        info = [(key, getattr(self, key)) for key in self.__dict__]
        info = ['{}={}'.format(key, size_repr(item)) for key, item in info]
        return '{}({})'.format(self.__class__.__name__, ', '.join(info))


class DataFlow(object):
    def __init__(self, n_id, flow='source_to_target'):
        self.n_id = n_id
        self.flow = flow
        self.__last_n_id__ = n_id
        self.blocks = []

    @property
    def batch_size(self):
        return self.n_id.size(0)

    def append(self, n_id, e_id, edge_index):
        i, j = (0, 1) if self.flow == 'target_to_source' else (1, 0)
        size = [None, None]
        size[i] = self.__last_n_id__.size(0)
        size[j] = n_id.size(0)
        block = Block(n_id, e_id, edge_index, tuple(size))
        self.blocks.append(block)
        self.__last_n_id__ = n_id

    def __len__(self):
        return len(self.blocks)

    def __getitem__(self, idx):
        return self.blocks[::-1][idx]

    def __iter__(self):
        for block in self.blocks[::-1]:
            yield block

    def to(self, device):
        for block in self.blocks:
            block.edge_index = block.edge_index.to(device)
        return self

    def __repr__(self):
        n_ids = [self.n_id] + [block.n_id for block in self.blocks]
        sep = '<-' if self.flow == 'source_to_target' else '->'
        info = sep.join([str(n_id.size(0)) for n_id in n_ids])
        return '{}({})'.format(self.__class__.__name__, info)


class NeighborSampler(object):
    r"""The neighbor sampler from the `"Inductive Representation Learning on
    Large Graphs" <https://arxiv.org/abs/1706.02216>`_ paper which iterates
    over graph nodes in a mini-batch fashion and constructs sampled subgraphs
    of size :obj:`num_hops`.

    It returns a generator of :obj:`DataFlow` that defines the message
    passing flow to the root nodes via a list of :obj:`num_hops` bipartite
    graph objects :obj:`edge_index` and the initial start nodes :obj:`n_id`.

    Args:
        data (torch_geometric.data.Data): The graph data object.
        size (int or float or [int] or [float]): The number of neighbors to
            sample (for each layer). The value of this parameter can be either
            set to be the same for each neighborhood or percentage-based.
        num_hops (int): The number of layers to sample.
        batch_size (int, optional): How many samples per batch to load.
            (default: :obj:`1`)
        shuffle (bool, optional): If set to :obj:`True`, the data will be
            reshuffled at every epoch. (default: :obj:`False`)
        drop_last (bool, optional): If set to :obj:`True`, will drop the last
            incomplete batch if the number of nodes is not divisible by the
            batch size. If set to :obj:`False` and the size of graph is not
            divisible by the batch size, the last batch will be smaller.
            (default: :obj:`False`)
        add_self_loops (bool, optional): If set to :obj:`True`, will add
            self-loops to each sampled neigborhood. (default: :obj:`False`)
        flow (string, optional): The flow direction of message passing
            (:obj:`"source_to_target"` or :obj:`"target_to_source"`).
            (default: :obj:`"source_to_target"`)
    """

    def __init__(self,
                 data,
                 size,
                 num_hops,
                 batch_size=1,
                 shuffle=False,
                 drop_last=False,
                 add_self_loops=False,
                 flow='source_to_target'):

        self.data = data
        self.size = repeat(size, num_hops)
        self.num_hops = num_hops
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.drop_last = drop_last
        self.add_self_loops = add_self_loops
        self.flow = flow

        assert flow in ['source_to_target', 'target_to_source']
        self.i, self.j = (0, 1) if flow == 'target_to_source' else (1, 0)

        self.edge_index_i, self.e_assoc = data.edge_index[self.i].sort()
        self.edge_index_j = data.edge_index[self.j, self.e_assoc]
        deg = degree(self.edge_index_i, data.num_nodes, dtype=torch.long)
        self.cumdeg = torch.cat([deg.new_zeros(1), deg.cumsum(0)])

        self.tmp = torch.empty(data.num_nodes, dtype=torch.long)

    def __get_batches__(self, subset=None):
        r"""Returns a list of mini-batches from the initial nodes in
        :obj:`subset`."""

        if subset is None and not self.shuffle:
            subset = torch.arange(self.data.num_nodes, dtype=torch.long)
        elif subset is None and self.shuffle:
            subset = torch.randperm(self.data.num_nodes)
        else:
            if subset.dtype == torch.uint8:
                subset = subset.nonzero().view(-1)
            if self.shuffle:
                subset = subset[torch.randperm(subset.size(0))]

        subsets = torch.split(subset, self.batch_size)
        if self.drop_last and subsets[-1].size(0) < self.batch_size:
            subsets = subsets[:-1]
        assert len(subsets) > 0
        return subsets

    def __produce__(self, n_id):
        r"""Produces a :obj:`DataFlow` object for a given mini-batch
        :obj:`n_id`."""

        data_flow = DataFlow(n_id, self.flow)

        for l in range(self.num_hops):
            e_id = neighbor_sampler(n_id, self.cumdeg, self.size[l])

            new_n_id = self.edge_index_j.index_select(0, e_id)
            if self.add_self_loops:
                new_n_id = torch.cat([new_n_id, n_id], dim=0)
            new_n_id = new_n_id.unique(sorted=False)
            e_id = self.e_assoc[e_id]

            edges = [None, None]

            edge_index_i = self.data.edge_index[self.i, e_id]
            if self.add_self_loops:
                edge_index_i = torch.cat([edge_index_i, n_id], dim=0)

            self.tmp[n_id] = torch.arange(n_id.size(0))
            edges[self.i] = self.tmp[edge_index_i]

            edge_index_j = self.data.edge_index[self.j, e_id]
            if self.add_self_loops:
                edge_index_j = torch.cat([edge_index_j, n_id], dim=0)

            self.tmp[new_n_id] = torch.arange(new_n_id.size(0))
            edges[self.j] = self.tmp[edge_index_j]

            edge_index = torch.stack(edges, dim=0)

            # Remove the edge identifier when adding self-loops to prevent
            # misused behavior.
            e_id = None if self.add_self_loops else e_id
            n_id = new_n_id

            data_flow.append(n_id, e_id, edge_index)

        return data_flow

    def __call__(self, subset=None):
        r"""Returns a generator of :obj:`DataFlow` that iterates over the nodes
        in :obj:`subset` in a mini-batch fashion.

        Args:
            subset (LongTensor or ByteTensor, optional): The initial nodes to
                propagete messages to. If set to :obj:`None`, will iterate over
                all nodes in the graph. (default: :obj:`None`)
        """
        for n_id in self.__get_batches__(subset):
            data_flow = self.__produce__(n_id)
            yield data_flow
