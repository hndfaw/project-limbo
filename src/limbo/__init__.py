"""Project Limbo pipeline engine."""

from limbo.engine import LocalExecutor
from limbo.spec import PipelineSpec, TaskSpec, load_pipeline

# Keep in sync with the version in pyproject.toml.
__version__ = "0.1.0"

__all__ = ["LocalExecutor", "PipelineSpec", "TaskSpec", "load_pipeline", "__version__"]
