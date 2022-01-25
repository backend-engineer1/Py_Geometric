import inspect
from dataclasses import dataclass, field, make_dataclass
from typing import Any, Dict, Optional, Union

from hydra.core.config_store import ConfigStore
from omegaconf import MISSING

import torch_geometric.datasets as datasets
import torch_geometric.transforms as transforms

EXCLUDE_ARG_NAMES = {'self', 'args', 'kwargs', 'pre_filter'}


def to_dataclass(cls: Any, base: Optional[Any] = None) -> Any:
    r"""Converts the input arguments of a given class :obj:`cls` to a
    :obj:`dataclass` schema, *e.g.*,

    .. code-block:: python

        from torch_geometric.transforms import NormalizeFeatures

        dataclass = to_dataclass(NormalizeFeatures)

    will generate

    .. code-block:: python

        @dataclass
        class NormalizeFeatures:
            _target_: str = "torch_geometric.transforms.NormalizeFeatures"
            attrs: List[str] = field(default_factory = lambda: ["x"])

    Args:
        cls (Any): The class to generate a schema for.
        base: (Any, optional): The base class of the schema.
            (default: :obj:`None`)
    """
    fields = []

    for name, arg in inspect.signature(cls.__init__).parameters.items():
        if name in EXCLUDE_ARG_NAMES:
            continue
        if base is not None and name in base.__dataclass_fields__.keys():
            continue

        item = (name, )

        annotation = arg.annotation
        if annotation != inspect.Parameter.empty:
            # `Union` types are not supported (except for `Optional`).
            # As such, we replace them with either `Any` or `Optional[Any]`.
            origin = getattr(annotation, '__origin__', None)
            args = getattr(annotation, '__args__', [])
            if origin == Union and type(None) in args and len(args) > 2:
                annotation = Optional[Any]
            elif origin == Union and type(None) not in args:
                annotation = Any
        else:
            annotation = Optional[Any]
        item = item + (annotation, )

        if arg.default != inspect.Parameter.empty:
            if isinstance(arg.default, (list, dict)):
                item = item + (field(default_factory=lambda: arg.default), )
            else:
                item = item + (arg.default, )
        else:
            item = item + (field(default=MISSING), )

        fields.append(item)

    full_cls_name = f'{cls.__module__}.{cls.__qualname__}'
    fields.append(('_target_', str, field(default=full_cls_name)))

    return make_dataclass(cls.__qualname__, fields=fields,
                          bases=() if base is None else (base, ))


cs = ConfigStore.instance()


@dataclass  # Register `torch_geometric.transforms` ###########################
class Transform:
    _target_: str = MISSING


for cls_name in set(transforms.__all__) - set([
        'BaseTransform',
        'Compose',
        'LinearTransformation',
        'AddMetaPaths',  # TODO
]):
    cls = to_dataclass(getattr(transforms, cls_name), base=Transform)
    # We use an explicit additional nesting level inside each config to allow
    # for applying multiple transformations.
    # https://hydra.cc/docs/patterns/select_multiple_configs_from_config_group
    cs.store(group='transform', name=cls_name, node={cls_name: cls})


@dataclass  # Register `torch_geometric.datasets` #############################
class Dataset:
    _target_: str = MISSING
    transform: Dict[str, Transform] = field(default_factory=dict)
    pre_transform: Dict[str, Transform] = field(default_factory=dict)


for cls_name in set(datasets.__all__) - set([]):
    cls = to_dataclass(getattr(datasets, cls_name), base=Dataset)
    cs.store(group='dataset', name=cls_name, node=cls)


@dataclass  # Register global schema ##########################################
class Config:
    dataset: Dataset = MISSING


cs.store(name='config', node=Config)
