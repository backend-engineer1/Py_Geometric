from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from torch import Tensor

from torch_geometric.typing import EdgeType, NodeType, OptTensor

# An input to a node-based sampler consists of two tensors:
#  * The example indices
#  * The node indices
#  * The timestamps of the given node indices (optional)
NodeSamplerInput = Tuple[Tensor, Tensor, OptTensor]

# An input to an edge-based sampler consists of four tensors:
#   * The example indices
#   * The row of the edge index in COO format
#   * The column of the edge index in COO format
#   * The labels of the edges
#   * The time attribute corresponding to the edge label (optional)
EdgeSamplerInput = Tuple[Tensor, Tensor, Tensor, Tensor, OptTensor]


# A sampler output contains the following information.
#   * node: a tensor of `n` output nodes resulting from sampling. In the
#       heterogeneous case, this is a dictionary mapping node types to the
#       associated output tensors, each with potentially varying length.
#   * row: a tensor of edge indices that correspond to the COO row values of
#       the edges in the sampled subgraph. Note that these indices must be
#       re-indexed from 0..n-1 corresponding to the nodes in the 'node' tensor.
#       In the heterogeneous case, this is a dictionary mapping edge types to
#       the associated COO row tensors.
#   * col: a tensor of edge indices that correspond to the COO column values of
#       the edges in the sampled subgraph. Note that these indices must be
#       re-indexed from 0..n-1 corresponding to the nodes in the 'node' tensor.
#       In the heterogeneous case, this is a dictionary mapping edge types to
#       the associated COO column tensors.
#   * edge: a tensor of the indices of the sampled edges in the original graph.
#       This tensor is used to obtain edge attributes from the original graph;
#       if no edge attributes are present, it may be omitted.
#   * batch: a tensor identifying the seed node for each sampled node.
#   * metadata: any additional metadata required by a loader using the sampler
#       output.
# There exist both homogeneous and heterogeneous versions.
@dataclass
class SamplerOutput:
    node: Tensor
    row: Tensor
    col: Tensor
    edge: Tensor
    batch: OptTensor = None
    # TODO(manan): refine this further; it does not currently define a proper
    # API for the expected output of a sampler.
    metadata: Optional[Any] = None


@dataclass
class HeteroSamplerOutput:
    node: Dict[NodeType, Tensor]
    row: Dict[EdgeType, Tensor]
    col: Dict[EdgeType, Tensor]
    edge: Dict[EdgeType, Tensor]
    batch: Optional[Dict[NodeType, Tensor]] = None
    # TODO(manan): refine this further; it does not currently define a proper
    # API for the expected output of a sampler.
    metadata: Optional[Any] = None


class BaseSampler(ABC):
    r"""A base class that initializes a graph sampler and provides
    :meth:`sample_from_nodes` and :meth:`sample_from_edges` routines.

    .. note ::

        Any data stored in the sampler will be *replicated* across data loading
        workers that use the sampler since each data loading worker holds its
        own instance of a sampler.
        As such, it is recommended to limit the amount of information stored in
        the sampler.
    """
    @abstractmethod
    def sample_from_nodes(
        self,
        index: NodeSamplerInput,
        **kwargs,
    ) -> Union[HeteroSamplerOutput, SamplerOutput]:
        r"""Performs sampling from the nodes specified in :obj:`index`,
        returning a sampled subgraph in the specified output format.

        Args:
            index (Tensor): The node indices to start sampling from.
        """
        pass

    @abstractmethod
    def sample_from_edges(
        self,
        index: EdgeSamplerInput,
        **kwargs,
    ) -> Union[HeteroSamplerOutput, SamplerOutput]:
        r"""Performs sampling from the edges specified in :obj:`index`,
        returning a sampled subgraph in the specified output format.

        Args:
            index (Tuple[Tensor, Tensor, Tensor, Optional[Tensor]]): The (1)
                source node indices, the (2) destination node indices, the (3)
                edge labels and the (4) optional timestamp of edges to start
                sampling from.
        """
        pass

    @property
    def edge_permutation(self) -> Union[OptTensor, Dict[EdgeType, OptTensor]]:
        r"""If the sampler performs any modification of edge ordering in the
        original graph, this function is expected to return the permutation
        tensor that defines the permutation from the edges in the original
        graph and the edges used in the sampler. If no such permutation was
        applied, :obj:`None` is returned. For heterogeneous graphs, the
        expected return type is a permutation tensor for each edge type."""
        return None
