"""Coach reasoning + executor + routing."""


class LLMError(Exception):
    """Raised when an LLM call fails after our retries."""
