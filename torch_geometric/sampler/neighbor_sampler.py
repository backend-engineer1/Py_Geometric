import math
from typing import Any, Callable, Dict, Optional, Tuple, Union

import torch
from torch import Tensor

from torch_geometric.data import Data, HeteroData, remote_backend_utils
from torch_geometric.data.feature_store import FeatureStore
from torch_geometric.data.graph_store import EdgeLayout, GraphStore
from torch_geometric.sampler.base import (
    BaseSampler,
    EdgeSamplerInput,
    HeteroSamplerOutput,
    NegativeSamplingConfig,
    NodeSamplerInput,
    SamplerOutput,
)
from torch_geometric.sampler.utils import remap_keys, to_csc, to_hetero_csc
from torch_geometric.typing import EdgeType, NodeType, NumNeighbors, OptTensor

try:
    import pyg_lib  # noqa
    _WITH_PYG_LIB = True
except ImportError:
    _WITH_PYG_LIB = False


class NeighborSampler(BaseSampler):
    r"""An implementation of an in-memory (heterogeneous) neighbor sampler used
    by :class:`~torch_geometric.loader.NeighborLoader`."""
    def __init__(
        self,
        data: Union[Data, HeteroData, Tuple[FeatureStore, GraphStore]],
        num_neighbors: NumNeighbors,
        replace: bool = False,
        directed: bool = True,
        disjoint: bool = False,
        temporal_strategy: str = 'uniform',
        input_type: Optional[Any] = None,
        time_attr: Optional[str] = None,
        is_sorted: bool = False,
        share_memory: bool = False,
    ):
        self.data_cls = data.__class__ if isinstance(
            data, (Data, HeteroData)) else 'custom'
        self.num_neighbors = num_neighbors
        self.replace = replace
        self.directed = directed
        self._disjoint = disjoint
        self.temporal_strategy = temporal_strategy
        self.node_time = None
        self.input_type = input_type

        # Set the number of source and destination nodes if we can, otherwise
        # ignore:
        self.num_src_nodes, self.num_dst_nodes = None, None
        if self.data_cls != 'custom' and issubclass(self.data_cls, Data):
            self.num_src_nodes = self.num_dst_nodes = data.num_nodes
        elif isinstance(self.input_type, tuple):
            if self.data_cls == 'custom':
                out = remote_backend_utils.size(*data, self.input_type)
                self.num_src_nodes, self.num_dst_nodes = out
            else:  # issubclass(self.data_cls, HeteroData):
                self.num_src_nodes = data[self.input_type[0]].num_nodes
                self.num_dst_nodes = data[self.input_type[-1]].num_nodes

        # TODO Unify the following conditionals behind the `FeatureStore`
        # and `GraphStore` API:

        # If we are working with a `Data` object, convert the edge_index to
        # CSC and store it:
        if isinstance(data, Data):
            if time_attr is not None:
                self.node_time = data[time_attr]

            # Convert the graph data into a suitable format for sampling.
            out = to_csc(data, device='cpu', share_memory=share_memory,
                         is_sorted=is_sorted, src_node_time=self.node_time)
            self.colptr, self.row, self.perm = out
            assert isinstance(num_neighbors, (list, tuple))

        # If we are working with a `HeteroData` object, convert each edge
        # type's edge_index to CSC and store it:
        elif isinstance(data, HeteroData):
            if time_attr is not None:
                self.node_time = data.collect(time_attr)

            self.node_types, self.edge_types = data.metadata()
            self._set_num_neighbors_and_num_hops(num_neighbors)

            assert input_type is not None
            self.input_type = input_type

            # Obtain CSC representations for in-memory sampling:
            out = to_hetero_csc(data, device='cpu', share_memory=share_memory,
                                is_sorted=is_sorted,
                                node_time_dict=self.node_time)
            colptr_dict, row_dict, perm_dict = out

            # Conversions to/from C++ string type:
            # Since C++ cannot take dictionaries with tuples as key as input,
            # edge type triplets need to be converted into single strings. This
            # is done by maintaining the following mappings:
            self.to_rel_type = {key: '__'.join(key) for key in self.edge_types}
            self.to_edge_type = {
                '__'.join(key): key
                for key in self.edge_types
            }

            self.row_dict = remap_keys(row_dict, self.to_rel_type)
            self.colptr_dict = remap_keys(colptr_dict, self.to_rel_type)
            self.num_neighbors = remap_keys(self.num_neighbors,
                                            self.to_rel_type)
            self.perm = perm_dict

        # If we are working with a `Tuple[FeatureStore, GraphStore]` object,
        # obtain edges from GraphStore and convert them to CSC if necessary,
        # storing the resulting representations:
        elif isinstance(data, tuple):
            # TODO support `FeatureStore` with no edge types (e.g. `Data`)
            feature_store, graph_store = data

            # Obtain all node and edge metadata:
            node_attrs = feature_store.get_all_tensor_attrs()
            edge_attrs = graph_store.get_all_edge_attrs()

            # TODO support `collect` on `FeatureStore`:
            if time_attr is not None:
                # If the `time_attr` is present, we expect that `GraphStore`
                # holds all edges sorted by destination, and within local
                # neighborhoods, node indices should be sorted by time.
                # TODO (matthias, manan) Find an alternative way to ensure
                for edge_attr in edge_attrs:
                    if edge_attr.layout == EdgeLayout.CSR:
                        raise ValueError(
                            "Temporal sampling requires that edges are stored "
                            "in either COO or CSC layout")
                    if not edge_attr.is_sorted:
                        raise ValueError(
                            "Temporal sampling requires that edges are "
                            "sorted by destination, and by source time "
                            "within local neighborhoods")

                # We need to obtain all features with 'attr_name=time_attr'
                # from the feature store and store them in node_time_dict. To
                # do so, we make an explicit feature store GET call here with
                # the relevant 'TensorAttr's
                time_attrs = [
                    attr for attr in node_attrs if attr.attr_name == time_attr
                ]
                for attr in time_attrs:
                    attr.index = None
                time_tensors = feature_store.multi_get_tensor(time_attrs)
                self.node_time = {
                    time_attr.group_name: time_tensor
                    for time_attr, time_tensor in zip(time_attrs, time_tensors)
                }

            self.node_types = list(
                set(node_attr.group_name for node_attr in node_attrs))
            self.edge_types = list(
                set(edge_attr.edge_type for edge_attr in edge_attrs))

            self._set_num_neighbors_and_num_hops(num_neighbors)

            assert input_type is not None
            self.input_type = input_type

            # Obtain CSC representations for in-memory sampling:
            row_dict, colptr_dict, perm_dict = graph_store.csc()

            self.to_rel_type = {key: '__'.join(key) for key in self.edge_types}
            self.to_edge_type = {
                '__'.join(key): key
                for key in self.edge_types
            }
            self.row_dict = remap_keys(row_dict, self.to_rel_type)
            self.colptr_dict = remap_keys(colptr_dict, self.to_rel_type)
            self.num_neighbors = remap_keys(self.num_neighbors,
                                            self.to_rel_type)
            self.perm = perm_dict

        else:
            raise TypeError(f"'{self.__class__.__name__}'' found invalid "
                            f"type: '{type(data)}'")

    def _set_num_neighbors_and_num_hops(self, num_neighbors):
        if isinstance(num_neighbors, (list, tuple)):
            num_neighbors = {key: num_neighbors for key in self.edge_types}
        assert isinstance(num_neighbors, dict)
        self.num_neighbors = num_neighbors

        # Add at least one element to the list to ensure `max` is well-defined
        self.num_hops = max([0] + [len(v) for v in num_neighbors.values()])

        for key, value in self.num_neighbors.items():
            if len(value) != self.num_hops:
                raise ValueError(f"Expected the edge type {key} to have "
                                 f"{self.num_hops} entries (got {len(value)})")

    @property
    def is_temporal(self) -> bool:
        return self.node_time is not None

    @property
    def disjoint(self) -> bool:
        return self._disjoint or self.is_temporal

    def _sample(
        self,
        seed: Union[torch.Tensor, Dict[NodeType, torch.Tensor]],
        **kwargs,
    ) -> Union[SamplerOutput, HeteroSamplerOutput]:
        r"""Implements neighbor sampling by calling :obj:`pyg-lib` or
        :obj:`torch-sparse` sampling routines, conditional on the type of
        :obj:`data` object.

        Note that the 'metadata' field of the output is not filled; it is the
        job of the caller to appropriately fill out this field for downstream
        loaders."""
        # TODO(manan): remote backends only support heterogeneous graphs:
        if self.data_cls == 'custom' or issubclass(self.data_cls, HeteroData):
            if _WITH_PYG_LIB:
                # TODO (matthias) `return_edge_id` if edge features present
                # TODO (matthias) Ideally, `seed` should inherit the type of
                # `colptr_dict` and `row_dict`.
                colptrs = list(self.colptr_dict.values())
                dtype = colptrs[0].dtype if len(colptrs) > 0 else torch.int64
                out = torch.ops.pyg.hetero_neighbor_sample(
                    self.node_types,
                    self.edge_types,
                    self.colptr_dict,
                    self.row_dict,
                    {k: v.to(dtype)
                     for k, v in seed.items()},  # seed_dict
                    self.num_neighbors,
                    self.node_time,
                    kwargs.get('seed_time_dict', None),
                    True,  # csc
                    self.replace,
                    self.directed,
                    self.disjoint,
                    self.temporal_strategy,
                    True,  # return_edge_id
                )
                row, col, node, edge, batch = out + (None, )
                if self.disjoint:
                    node = {k: v.t().contiguous() for k, v in node.items()}
                    batch = {k: v[0] for k, v in node.items()}
                    node = {k: v[1] for k, v in node.items()}

            else:
                if self.disjoint:
                    raise ValueError("'disjoint' sampling not supported for "
                                     "neighbor sampling via 'torch-sparse'. "
                                     "Please install 'pyg-lib' for improved "
                                     "and optimized sampling routines.")
                out = torch.ops.torch_sparse.hetero_neighbor_sample(
                    self.node_types,
                    self.edge_types,
                    self.colptr_dict,
                    self.row_dict,
                    seed,  # seed_dict
                    self.num_neighbors,
                    self.num_hops,
                    self.replace,
                    self.directed,
                )
                node, row, col, edge, batch = out + (None, )

            return HeteroSamplerOutput(
                node=node,
                row=remap_keys(row, self.to_edge_type),
                col=remap_keys(col, self.to_edge_type),
                edge=remap_keys(edge, self.to_edge_type),
                batch=batch,
            )

        if issubclass(self.data_cls, Data):
            if _WITH_PYG_LIB:
                # TODO (matthias) `return_edge_id` if edge features present
                # TODO (matthias) Ideally, `seed` should inherit the type of
                # `colptr` and `row`.
                out = torch.ops.pyg.neighbor_sample(
                    self.colptr,
                    self.row,
                    seed.to(self.colptr.dtype),  # seed
                    self.num_neighbors,
                    self.node_time,
                    kwargs.get('seed_time', None),
                    True,  # csc
                    self.replace,
                    self.directed,
                    self.disjoint,
                    self.temporal_strategy,
                    True,  # return_edge_id
                )
                row, col, node, edge, batch = out + (None, )
                if self.disjoint:
                    batch, node = node.t().contiguous()

            else:
                if self.disjoint:
                    raise ValueError("'disjoint' sampling not supported for "
                                     "neighbor sampling via 'torch-sparse'. "
                                     "Please install 'pyg-lib' for improved "
                                     "and optimized sampling routines.")
                out = torch.ops.torch_sparse.neighbor_sample(
                    self.colptr,
                    self.row,
                    seed,  # seed
                    self.num_neighbors,
                    self.replace,
                    self.directed,
                )
                node, row, col, edge, batch = out + (None, )

            return SamplerOutput(
                node=node,
                row=row,
                col=col,
                edge=edge,
                batch=batch,
            )

        raise TypeError(f"'{self.__class__.__name__}'' found invalid "
                        f"type: '{self.data_cls}'")

    # Node-based sampling #####################################################

    def sample_from_nodes(
        self,
        index: NodeSamplerInput,
        **kwargs,
    ) -> Union[SamplerOutput, HeteroSamplerOutput]:
        return node_sample(index, self._sample, self.input_type, **kwargs)

    # Edge-based sampling #####################################################

    def sample_from_edges(
        self,
        index: EdgeSamplerInput,
        **kwargs,
    ) -> Union[SamplerOutput, HeteroSamplerOutput]:
        return edge_sample(index, self._sample, self.num_src_nodes,
                           self.num_dst_nodes, self.disjoint, self.input_type,
                           node_time=self.node_time, **kwargs)

    # Other Utilities #########################################################

    @property
    def edge_permutation(self) -> Union[OptTensor, Dict[EdgeType, OptTensor]]:
        return self.perm


# Sampling Utilities ##########################################################


def node_sample(
    index: NodeSamplerInput,
    sample_fn: Callable,
    input_type: Optional[str] = None,
    **kwargs,
) -> Union[SamplerOutput, HeteroSamplerOutput]:
    r"""Performs sampling from a node sampler input, leveraging a sampling
    function that accepts a seed and (optionally) a seed time / seed time
    dictionary as input. Returns the output of this sampling procedure."""
    index, input_nodes, input_time = index

    if input_type is not None:
        # Heterogeneous sampling:
        seed_time_dict = None
        if input_time is not None:
            seed_time_dict = {input_type: input_time}
        output = sample_fn(seed={input_type: input_nodes},
                           seed_time_dict=seed_time_dict)
        output.metadata = index

    else:
        # Homogeneous sampling:
        output = sample_fn(seed=input_nodes, seed_time=input_time)
        output.metadata = index

    return output


def edge_sample(
    index: EdgeSamplerInput,
    sample_fn: Callable,
    num_src_nodes: int,
    num_dst_nodes: int,
    disjoint: bool,
    input_type: Optional[Tuple[str, str, str]] = None,
    node_time: Optional[Union[Tensor, Dict[str, Tensor]]] = None,
    neg_sampling: Optional[NegativeSamplingConfig] = None,
) -> Union[SamplerOutput, HeteroSamplerOutput]:
    r"""Performs sampling from an edge sampler input, leveraging a sampling
    function of the same signature as `node_sample`."""
    index, src, dst, edge_label, edge_label_time = index
    src_time = dst_time = edge_label_time

    assert edge_label_time is None or disjoint

    num_pos = src.numel()
    num_neg = 0

    # Negative Sampling #######################################################

    if neg_sampling is not None:
        # When we are doing negative sampling, we append negative information
        # of nodes/edges to `src`, `dst`, `src_time`, `dst_time`.
        # Later on, we can easily reconstruct what belongs to positive and
        # negative examples by slicing via `num_pos`.
        num_neg = math.ceil(num_pos * neg_sampling.amount)

        if neg_sampling.is_binary():
            # In the "binary" case, we randomly sample negative pairs of nodes.
            if isinstance(node_time, dict):
                src_node_time = node_time.get(input_type[0])
            else:
                src_node_time = node_time

            src_neg = neg_sample(src, neg_sampling.amount, num_src_nodes,
                                 src_time, src_node_time)
            src = torch.cat([src, src_neg], dim=0)

            if isinstance(node_time, dict):
                dst_node_time = node_time.get(input_type[-1])
            else:
                dst_node_time = node_time

            dst_neg = neg_sample(dst, neg_sampling.amount, num_dst_nodes,
                                 dst_time, dst_node_time)
            dst = torch.cat([dst, dst_neg], dim=0)

            if edge_label is None:
                edge_label = torch.ones(num_pos)
            size = (num_neg, ) + edge_label.size()[1:]
            edge_neg_label = edge_label.new_zeros(size)
            edge_label = torch.cat([edge_label, edge_neg_label])

            if edge_label_time is not None:
                src_time = dst_time = edge_label_time.repeat(
                    1 + math.ceil(neg_sampling.amount))[:num_pos + num_neg]

        elif neg_sampling.is_triplet():
            # In the "triplet" case, we randomly sample negative destinations.
            if isinstance(node_time, dict):
                dst_node_time = node_time.get(input_type[-1])
            else:
                dst_node_time = node_time

            dst_neg = neg_sample(dst, neg_sampling.amount, num_dst_nodes,
                                 dst_time, dst_node_time)
            dst = torch.cat([dst, dst_neg], dim=0)

            assert edge_label is None

            if edge_label_time is not None:
                dst_time = edge_label_time.repeat(1 + neg_sampling.amount)

    # Heterogeneus Neighborhood Sampling ######################################

    if input_type is not None:
        seed_time_dict = None
        if input_type[0] != input_type[-1]:  # Two distinct node types:

            if not disjoint:
                src, inverse_src = src.unique(return_inverse=True)
                dst, inverse_dst = dst.unique(return_inverse=True)

            seed_dict = {input_type[0]: src, input_type[-1]: dst}

            if edge_label_time is not None:  # Always disjoint.
                seed_time_dict = {
                    input_type[0]: src_time,
                    input_type[-1]: dst_time,
                }

        else:  # Only a single node type: Merge both source and destination.

            seed = torch.cat([src, dst], dim=0)

            if not disjoint:
                seed, inverse_seed = seed.unique(return_inverse=True)

            seed_dict = {input_type[0]: seed}

            if edge_label_time is not None:  # Always disjoint.
                seed_time_dict = {
                    input_type[0]: torch.cat([src_time, dst_time], dim=0),
                }

        out = sample_fn(seed=seed_dict, seed_time_dict=seed_time_dict)

        # Enhance `out` by label information ##################################
        if disjoint:
            for key, batch in out.batch.items():
                out.batch[key] = batch % num_pos

        if neg_sampling is None or neg_sampling.is_binary():
            if disjoint:
                if input_type[0] != input_type[-1]:
                    edge_label_index = torch.arange(num_pos + num_neg)
                    edge_label_index = edge_label_index.repeat(2).view(2, -1)
                else:
                    edge_label_index = torch.arange(2 * (num_pos + num_neg))
                    edge_label_index = edge_label_index.view(2, -1)
            else:
                if input_type[0] != input_type[-1]:
                    edge_label_index = torch.stack([
                        inverse_src,
                        inverse_dst,
                    ], dim=0)
                else:
                    edge_label_index = inverse_seed.view(2, -1)

            out.metadata = (index, edge_label_index, edge_label, src_time)

        elif neg_sampling.is_triplet():
            if disjoint:
                src_index = torch.arange(num_pos)
                if input_type[0] != input_type[-1]:
                    dst_pos_index = torch.arange(num_pos)
                    # `dst_neg_index` needs to be offset such that indices with
                    # offset `num_pos` belong to the same triplet:
                    dst_neg_index = torch.arange(
                        num_pos, seed_dict[input_type[-1]].numel())
                    dst_neg_index = dst_neg_index.view(-1, num_pos).t()
                else:
                    dst_pos_index = torch.arange(num_pos, 2 * num_pos)
                    dst_neg_index = torch.arange(
                        2 * num_pos, seed_dict[input_type[-1]].numel())
                    dst_neg_index = dst_neg_index.view(-1, num_pos).t()
            else:
                if input_type[0] != input_type[-1]:
                    src_index = inverse_src
                    dst_pos_index = inverse_dst[:num_pos]
                    dst_neg_index = inverse_dst[num_pos:]
                else:
                    src_index = inverse_seed[:num_pos]
                    dst_pos_index = inverse_seed[num_pos:2 * num_pos]
                    dst_neg_index = inverse_seed[2 * num_pos:]

            dst_neg_index = dst_neg_index.view(num_pos, -1).squeeze(-1)

            out.metadata = (index, src_index, dst_pos_index, dst_neg_index,
                            src_time)

    # Homogeneus Neighborhood Sampling ########################################

    else:

        seed = torch.cat([src, dst], dim=0)
        seed_time = None

        if not disjoint:
            seed, inverse_seed = seed.unique(return_inverse=True)

        if edge_label_time is not None:  # Always disjoint.
            seed_time = torch.cat([src_time, dst_time])

        out = sample_fn(seed=seed, seed_time=seed_time)

        # Enhance `out` by label information ##################################
        if neg_sampling is None or neg_sampling.is_binary():
            if disjoint:
                out.batch = out.batch % num_pos
                edge_label_index = torch.arange(2 * seed.numel()).view(2, -1)
            else:
                edge_label_index = inverse_seed.view(2, -1)

            out.metadata = (index, edge_label_index, edge_label, seed_time)

        elif neg_sampling.is_triplet():
            if disjoint:
                out.batch = out.batch % num_pos
                src_index = torch.arange(num_pos)
                dst_pos_index = torch.arange(num_pos, 2 * num_pos)
                # `dst_neg_index` needs to be offset such that indices with
                # offset `num_pos` belong to the same triplet:
                dst_neg_index = torch.arange(2 * num_pos, seed.numel())
                dst_neg_index = dst_neg_index.view(-1, num_pos).t()
            else:
                src_index = inverse_seed[:num_pos]
                dst_pos_index = inverse_seed[num_pos:2 * num_pos]
                dst_neg_index = inverse_seed[2 * num_pos:]
            dst_neg_index = dst_neg_index.view(num_pos, -1).squeeze(-1)

            out.metadata = (index, src_index, dst_pos_index, dst_neg_index,
                            src_time)

    return out


def neg_sample(seed: Tensor, num_samples: Union[int, float], num_nodes: int,
               seed_time: Optional[Tensor],
               node_time: Optional[Tensor]) -> Tensor:
    num_neg = math.ceil(seed.numel() * num_samples)

    # TODO: Do not sample false negatives.
    if node_time is None:
        return torch.randint(num_nodes, (num_neg, ))

    # If we are in a temporal-sampling scenario, we need to respect the
    # timestamp of the given nodes we can use as negative examples.
    # That is, we can only sample nodes for which `node_time < seed_time`.
    # For now, we use a greedy algorithm which randomly samples negative
    # nodes and discard any which do not respect the temporal constraint.
    # We iteratively repeat this process until we have sampled a valid node for
    # each seed.
    # TODO See if this greedy algorithm here can be improved.
    assert seed_time is not None
    num_samples = math.ceil(num_samples)
    seed_time = seed_time.view(1, -1).expand(num_samples, -1)
    out = torch.randint(num_nodes, (num_samples, seed.numel()))
    mask = node_time[out] >= seed_time
    neg_sampling_complete = False
    for i in range(5):  # pragma: no cover
        if not mask.any():
            neg_sampling_complete = True
            break

        # Greedily search for alternative negatives.
        numel = int(mask.sum())
        out[mask] = tmp = torch.randint(num_nodes, (numel, ))
        mask[mask.clone()] = node_time[tmp] >= seed_time[mask]

    if not neg_sampling_complete:  # pragma: no cover
        # Not much options left. In that case, we set remaining negatives
        # to the node with minimum timestamp.
        out[mask] = node_time.argmin()

    return out.view(-1)[:num_neg]
