from typing import Any, Callable, Iterator, List, Optional, Tuple, Union

import torch

from torch_geometric.data import Data, HeteroData
from torch_geometric.data.feature_store import FeatureStore
from torch_geometric.data.graph_store import GraphStore
from torch_geometric.loader.base import DataLoaderIterator
from torch_geometric.loader.utils import (
    InputData,
    filter_custom_store,
    filter_data,
    filter_hetero_data,
    get_edge_label_index,
)
from torch_geometric.sampler.base import (
    BaseSampler,
    EdgeSamplerInput,
    HeteroSamplerOutput,
    NegativeSamplingConfig,
    SamplerOutput,
)
from torch_geometric.typing import InputEdges, OptTensor


class LinkLoader(torch.utils.data.DataLoader):
    r"""A data loader that performs neighbor sampling from link information,
    using a generic :class:`~torch_geometric.sampler.BaseSampler`
    implementation that defines a :meth:`sample_from_edges` function and is
    supported on the provided input :obj:`data` object.

    .. note::
        Negative sampling is currently implemented in an approximate
        way, *i.e.* negative edges may contain false negatives.

    Args:
        data (torch_geometric.data.Data or torch_geometric.data.HeteroData):
            The :class:`~torch_geometric.data.Data` or
            :class:`~torch_geometric.data.HeteroData` graph object.
        link_sampler (torch_geometric.sampler.BaseSampler): The sampler
            implementation to be used with this loader. Note that the
            sampler implementation must be compatible with the input data
            object.
        edge_label_index (Tensor or EdgeType or Tuple[EdgeType, Tensor]):
            The edge indices for which neighbors are sampled to create
            mini-batches.
            If set to :obj:`None`, all edges will be considered.
            In heterogeneous graphs, needs to be passed as a tuple that holds
            the edge type and corresponding edge indices.
            (default: :obj:`None`)
        edge_label (Tensor, optional): The labels of edge indices for
            which neighbors are sampled. Must be the same length as
            the :obj:`edge_label_index`. If set to :obj:`None` its set to
            `torch.zeros(...)` internally. (default: :obj:`None`)
        edge_label_time (Tensor, optional): The timestamps for edge indices
            for which neighbors are sampled. Must be the same length as
            :obj:`edge_label_index`. If set, temporal sampling will be
            used such that neighbors are guaranteed to fulfill temporal
            constraints, *i.e.*, neighbors have an earlier timestamp than
            the ouput edge. The :obj:`time_attr` needs to be set for this
            to work. (default: :obj:`None`)
        neg_sampling (NegativeSamplingConfig, optional): The negative sampling
            strategy. Can be either :obj:`"binary"` or :obj:`"triplet"`, and
            can be further customized by an additional :obj:`amount` argument
            to control the ratio of sampled negatives to positive edges.
            If set to :obj:`"binary"`, will randomly sample negative links
            from the graph.
            In case :obj:`edge_label` does not exist, it will be automatically
            created and represents a binary classification task (:obj:`0` =
            negative edge, :obj:`1` = positive edge).
            In case :obj:`edge_label` does exist, it has to be a categorical
            label from :obj:`0` to :obj:`num_classes - 1`.
            After negative sampling, label :obj:`0` represents negative edges,
            and labels :obj:`1` to :obj:`num_classes` represent the labels of
            positive edges.
            Note that returned labels are of type :obj:`torch.float` for binary
            classification (to facilitate the ease-of-use of
            :meth:`F.binary_cross_entropy`) and of type
            :obj:`torch.long` for multi-class classification (to facilitate the
            ease-of-use of :meth:`F.cross_entropy`).
            If set to :obj:`"triplet"`, will randomly sample negative
            destination nodes for each positive source node.
            Samples can be accessed via the attributes :obj:`src_index`,
            :obj:`dst_pos_index` and :obj:`dst_neg_index` in the respective
            node types of the returned mini-batch.
            :obj:`edge_label` needs to be :obj:`None` for
            :obj:`"triplet"`-based negative sampling.
            If set to :obj:`None`, no negative sampling strategy is applied.
            (default: :obj:`None`)
        neg_sampling_ratio (int or float, optional): The ratio of sampled
            negative edges to the number of positive edges.
            Deprecated in favor of the :obj:`neg_sampling` argument.
            (default: :obj:`None`).
        transform (Callable, optional): A function/transform that takes in
            a sampled mini-batch and returns a transformed version.
            (default: :obj:`None`)
        filter_per_worker (bool, optional): If set to :obj:`True`, will filter
            the returning data in each worker's subprocess rather than in the
            main process.
            Setting this to :obj:`True` is generally not recommended:
            (1) it may result in too many open file handles,
            (2) it may slown down data loading,
            (3) it requires operating on CPU tensors.
            (default: :obj:`False`)
        **kwargs (optional): Additional arguments of
            :class:`torch.utils.data.DataLoader`, such as :obj:`batch_size`,
            :obj:`shuffle`, :obj:`drop_last` or :obj:`num_workers`.
    """
    def __init__(
        self,
        data: Union[Data, HeteroData, Tuple[FeatureStore, GraphStore]],
        link_sampler: BaseSampler,
        edge_label_index: InputEdges = None,
        edge_label: OptTensor = None,
        edge_label_time: OptTensor = None,
        neg_sampling: Optional[NegativeSamplingConfig] = None,
        neg_sampling_ratio: Optional[Union[int, float]] = None,
        transform: Callable = None,
        filter_per_worker: bool = False,
        **kwargs,
    ):
        # Remove for PyTorch Lightning:
        if 'dataset' in kwargs:
            del kwargs['dataset']
        if 'collate_fn' in kwargs:
            del kwargs['collate_fn']

        if neg_sampling_ratio is not None and neg_sampling_ratio != 0.0:
            # TODO: Deprecation warning.
            neg_sampling = NegativeSamplingConfig("binary", neg_sampling_ratio)

        # Get edge type (or `None` for homogeneous graphs):
        edge_type, edge_label_index = get_edge_label_index(
            data, edge_label_index)

        self.data = data
        self.edge_type = edge_type
        self.link_sampler = link_sampler
        self.neg_sampling = NegativeSamplingConfig.cast(neg_sampling)
        self.transform = transform
        self.filter_per_worker = filter_per_worker

        if (self.neg_sampling is not None and self.neg_sampling.is_binary()
                and edge_label is not None and edge_label.min() == 0):
            # Increment labels such that `zero` now denotes "negative".
            edge_label = edge_label + 1

        if (self.neg_sampling is not None and self.neg_sampling.is_triplet()
                and edge_label is not None):
            raise ValueError("'edge_label' needs to be undefined for "
                             "'triplet'-based negative sampling. Please use "
                             "`src_index`, `dst_pos_index` and "
                             "`neg_pos_index` of the returned mini-batch "
                             "instead to differentiate between positive and "
                             "negative samples.")

        self.input_data = InputData(
            edge_label_index[0].clone(),
            edge_label_index[1].clone(),
            edge_label,
            edge_label_time,
        )

        iterator = range(edge_label_index.size(1))
        super().__init__(iterator, collate_fn=self.collate_fn, **kwargs)

    def collate_fn(self, index: List[int]) -> Any:
        r"""Samples a subgraph from a batch of input nodes."""
        input_data: EdgeSamplerInput = self.input_data[index]

        out = self.link_sampler.sample_from_edges(
            input_data, neg_sampling=self.neg_sampling)

        if self.filter_per_worker:  # Execute `filter_fn` in the worker process
            out = self.filter_fn(out)

        return out

    def filter_fn(
        self,
        out: Union[SamplerOutput, HeteroSamplerOutput],
    ) -> Union[Data, HeteroData]:
        r"""Joins the sampled nodes with their corresponding features,
        returning the resulting :class:`~torch_geometric.data.Data` or
        :class:`~torch_geometric.data.HeteroData` object to be used downstream.
        """
        if isinstance(out, SamplerOutput):
            data = filter_data(self.data, out.node, out.row, out.col, out.edge,
                               self.link_sampler.edge_permutation)

            data.batch = out.batch
            data.input_id = out.metadata[0]

            if self.neg_sampling is None or self.neg_sampling.is_binary():
                data.edge_label_index = out.metadata[1]
                data.edge_label = out.metadata[2]
                data.edge_label_time = out.metadata[3]
            elif self.neg_sampling.is_triplet():
                data.src_index = out.metadata[1]
                data.dst_pos_index = out.metadata[2]
                data.dst_neg_index = out.metadata[3]
                data.seed_time = out.metadata[4]

        elif isinstance(out, HeteroSamplerOutput):
            if isinstance(self.data, HeteroData):
                data = filter_hetero_data(self.data, out.node, out.row,
                                          out.col, out.edge,
                                          self.link_sampler.edge_permutation)
            else:  # Tuple[FeatureStore, GraphStore]
                data = filter_custom_store(*self.data, out.node, out.row,
                                           out.col, out.edge)

            for key, batch in (out.batch or {}).items():
                data[key].batch = batch

            data[self.edge_type].input_id = out.metadata[0]

            if self.neg_sampling is None or self.neg_sampling.is_binary():
                data[self.edge_type].edge_label_index = out.metadata[1]
                data[self.edge_type].edge_label = out.metadata[2]
                data[self.edge_type].edge_label_time = out.metadata[3]
            elif self.neg_sampling.is_triplet():
                data[self.edge_type[0]].src_index = out.metadata[1]
                data[self.edge_type[-1]].dst_pos_index = out.metadata[2]
                data[self.edge_type[-1]].dst_neg_index = out.metadata[3]
                data[self.edge_type[0]].seed_time = out.metadata[4]
                data[self.edge_type[-1]].seed_time = out.metadata[4]

        else:
            raise TypeError(f"'{self.__class__.__name__}'' found invalid "
                            f"type: '{type(out)}'")

        return data if self.transform is None else self.transform(data)

    def _get_iterator(self) -> Iterator:
        if self.filter_per_worker:
            return super()._get_iterator()

        # Execute `filter_fn` in the main process:
        return DataLoaderIterator(super()._get_iterator(), self.filter_fn)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}()'
