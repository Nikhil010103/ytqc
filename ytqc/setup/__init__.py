"""Setup wizard: gets a non-technical QC user from zero to a working tool —
Ollama + model, kimi-webbridge daemon + Chrome extension, plus the VidIQ and
Adblock-for-YouTube extensions (force-installed via Chrome managed policy), config,
then the chat assistant.

Cross-platform (macOS + Windows). Every step is idempotent (detect → act →
verify) so `ytqc setup` can be re-run safely and only fixes what's still broken."""
from ytqc.setup.platform import StepResult, Status

__all__ = ["StepResult", "Status"]
