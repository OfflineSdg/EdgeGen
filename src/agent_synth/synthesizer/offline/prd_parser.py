class PRDParser:
    """Parse PRD content for use as LLM context."""

    def __init__(self, prd_content: str):
        self._raw_text = prd_content

    def get_domain_context(self) -> str:
        """Get domain context string for LLM prompts."""
        return self._raw_text
