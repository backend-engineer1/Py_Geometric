from setuptools import setup, find_packages

__version__ = '2.0.1'
url = 'https://github.com/pyg-team/pytorch_geometric'

install_requires = [
    'numpy',
    'tqdm',
    'scipy',
    'networkx',
    'scikit-learn',
    'requests',
    'pandas',
    'rdflib',
    'googledrivedownloader',
    'jinja2',
    'pyparsing',
    'yacs',
    'PyYAML',
]
setup_requires = []
tests_require = ['pytest', 'pytest-runner', 'pytest-cov', 'mock']

setup(
    name='torch_geometric',
    version=__version__,
    description='Graph Neural Network Library for PyTorch',
    author='Matthias Fey',
    author_email='matthias.fey@tu-dortmund.de',
    url=url,
    download_url='{}/archive/{}.tar.gz'.format(url, __version__),
    keywords=[
        'deep-learning'
        'pytorch',
        'geometric-deep-learning',
        'graph-neural-networks',
        'graph-convolutional-networks',
    ],
    python_requires='>=3.6',
    install_requires=install_requires,
    setup_requires=setup_requires,
    tests_require=tests_require,
    extras_require={'test': tests_require},
    packages=find_packages(),
    include_package_data=True,
)
