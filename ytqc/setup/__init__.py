"""Setup wizard: gets a non-technical QC user from zero to a working tool —
Ollama + model, kimi-webbridge daemon + Chrome extension, the VidIQ extension
(force-installed via Chrome managed policy), config, then the chat assistant.

Cross-platform (macOS + Windows). Every step is idempotent (detect → act →
verify) so `ytqc setup` can be re-run safely and `--repair` fixes just what's broken."""
from ytqc.setup.platform import StepResult, Status

__all__ = ["StepResult", "Status"]
