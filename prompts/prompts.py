"""Prompt template management utilities."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any

from src.logging_config import get_logger

logger = get_logger(__name__)

SEC10K_TEMPLATES_DIR = Path(__file__).parent / "templates" / "sec10k"
XBRL_TEMPLATES_DIR = Path(__file__).parent / "templates" / "xbrl"



# NB: the flat XbrlPrompts enum was removed in the LLM-harness refactor (Phase 3).
# XBRL prompts now live under prompts/templates/xbrl/<location>/<stage>/<version>/
# and are resolved by provider/series/version via prompts.prompt_registry
# (PromptRegistry / get_xbrl_registry), not by flat filename stem.


class Sec10KPrompts(Enum):
    TABLE_SUMMARY_BASIC = "sec10k_table_summary_basic"
    TABLE_SUMMARY_DETAILED = "sec10k_table_summary_detailed"
    TABLE_SUMMARY_AND_DATA = "sec10k_table_summary_and_data"
    TABLE_SUMMARY_SYSTEM_PROMPT = "sec10k_table_summary_system_prompt"
    PAGE_SUMMARY_BASIC = "sec10k_page_summary_basic"
    QUERY_ROUTER_SYSTEM = "sec10k_query_rewrite_router_system"
    QUERY_ROUTER_USER = "sec10k_query_rewrite_router_user"
    QUERY_DECOMPOSE_SYSTEM = "sec10k_query_decomp_system"
    QUERY_DECOMPOSE_USER = "sec10k_query_decomp_user"
    QUERY_PARAPHRASE_SYSTEM = "sec10k_query_paraphrase_system"
    QUERY_PARAPHRASE_USER = "sec10k_query_paraphrase_user"
    HYDE_GENERATOR_SYSTEM = "sec10k_hyde_generator_system"
    HYDE_GENERATOR_USER = "sec10k_hyde_generator_user"
    RAG_RESULT_SYSTEM = "sec10k_rag_query_result_system"
    RAG_RESULT_USER = "sec10k_rag_query_result_user"


class PromptTemplate:
    """Simple prompt template with variable substitution.

    Example:
        >>> template = PromptTemplate(
        ...     "Answer this question: {question}\\nContext: {context}"
        ... )
        >>> prompt = template.format(question="What is RAG?", context="...")
    """

    def __init__(self, template: str, name: str | None = None):
        """Initialize prompt template.

        Args:
            template: Template string with {variable} placeholders
            name: Optional name for the template
        """
        self.template = template
        self.name = name or "unnamed"
        self._variables = self._extract_variables()

    def format(self, **kwargs: Any) -> str:
        """Format template with provided variables.

        Args:
            **kwargs: Variable values to substitute

        Returns:
            Formatted prompt string

        Raises:
            ValueError: If required variables are missing
        """
        missing = self._variables - set(kwargs.keys())
        if missing:
            raise ValueError(f"Missing required variables: {missing}")

        return self.template.format(**kwargs)

    def _extract_variables(self) -> set[str]:
        """Extract variable names from template."""
        import re
        return set(re.findall(r"\{(\w+)\}", self.template))

    @property
    def variables(self) -> set[str]:
        """Get required variable names."""
        return self._variables

    @property
    def content_hash(self) -> str:
        """Short, stable hash of the raw template text (prompt versioning).

        Changes whenever the template source changes, so a logged ``llm_call``
        can be tied to the exact prompt that produced it (Phase 4 reproducibility).
        """
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()[:12]

    def __repr__(self) -> str:
        return f"PromptTemplate(name={self.name}, variables={self.variables})"


class PromptManager:
    """Manage a collection of prompt templates.

    Supports loading from files and storing templates by name.

    Example:
        >>> manager = PromptManager()
        >>> manager.add("qa", "Question: {question}\\nAnswer:")
        >>> prompt = manager.format("qa", question="What is RAG?")
    """

    def __init__(self, templates_dir: str | Path | None = None):
        """Initialize prompt manager.

        Args:
            templates_dir: Optional directory to load templates from
        """
        self._templates: dict[str, PromptTemplate] = {}
        self.templates_dir = Path(templates_dir) if templates_dir else None

        if self.templates_dir and self.templates_dir.exists():
            self.load_from_directory(self.templates_dir)

    def add(self, name: str, template: str | PromptTemplate) -> None:
        """Add a template to the manager.

        Args:
            name: Template name
            template: Template string or PromptTemplate instance
        """
        if isinstance(template, str):
            template = PromptTemplate(template, name=name)
        self._templates[name] = template
        logger.debug(f"Added template '{name}' with variables: {template.variables}")

    def get(self, name: str) -> PromptTemplate:
        """Get a template by name.

        Args:
            name: Template name

        Returns:
            PromptTemplate instance

        Raises:
            KeyError: If template not found
        """
        if name not in self._templates:
            raise KeyError(f"Template '{name}' not found. Available: {list(self._templates.keys())}")
        return self._templates[name]

    def format(self, name: str, /, **kwargs: Any) -> str:
        """Format a template by name.

        ``name`` is positional-only so a template may itself declare a
        ``{name}`` variable (passed via ``**kwargs``) without colliding with the
        template-lookup argument.

        Args:
            name: Template name
            **kwargs: Variable values

        Returns:
            Formatted prompt string
        """
        template = self.get(name)
        return template.format(**kwargs)

    def list_templates(self) -> list[str]:
        """List all available template names."""
        return list(self._templates.keys())

    def version(self, *names: str) -> str:
        """Return a stable short hash over the raw text of the named templates.

        Order-independent (names are sorted) and includes the template names, so
        the value changes if any referenced template's content changes. Used to
        stamp each LLM call with the version of the prompt(s) that produced it.
        """
        h = hashlib.sha256()
        for name in sorted(names):
            h.update(name.encode("utf-8"))
            h.update(b"\0")
            h.update(self.get(name).template.encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()[:12]

    def signature(self, *names: str) -> dict[str, Any]:
        """``{"templates": [...names...], "version": "<hash>"}`` for the named templates."""
        return {"templates": list(names), "version": self.version(*names)}

    def load_from_directory(self, directory: str | Path) -> None:
        """Load all .txt templates from a directory.

        Template files should be named <name>.txt and contain the template text.

        Args:
            directory: Directory path containing template files
        """
        directory = Path(directory)
        if not directory.exists():
            logger.warning(f"Templates directory not found: {directory}")
            return

        for file_path in directory.glob("*.txt"):
            name = file_path.stem
            with open(file_path, encoding="utf-8") as f:
                template_text = f.read()
            self.add(name, template_text)
            logger.debug(f"Loaded template '{name}' from {file_path}")

    def load_from_json(self, json_path: str | Path) -> None:
        """Load templates from a JSON file.

        JSON format: {"template_name": "template string", ...}

        Args:
            json_path: Path to JSON file
        """
        json_path = Path(json_path)
        if not json_path.exists():
            logger.warning(f"JSON templates file not found: {json_path}")
            return

        with open(json_path, encoding="utf-8") as f:
            templates_dict = json.load(f)

        for name, template_text in templates_dict.items():
            self.add(name, template_text)
            logger.info(f"Loaded template '{name}' from JSON")

    def save_to_json(self, json_path: str | Path) -> None:
        """Save all templates to a JSON file.

        Args:
            json_path: Output JSON file path
        """
        json_path = Path(json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        templates_dict = {name: tmpl.template for name, tmpl in self._templates.items()}

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(templates_dict, f, indent=2)

        logger.info(f"Saved {len(templates_dict)} templates to {json_path}")


# Predefined common templates
DEFAULT_TEMPLATES = {
    "qa": """Answer the following question based on the provided context.

Context: {context}

Question: {question}

Answer:""",
    "summarize": """Summarize the following text concisely:

{text}

Summary:""",
    "rag_qa": """You are a helpful assistant. Answer the question based on the context below.

Context:
{context}

Question: {question}

Provide a clear and concise answer based only on the context provided. If the context doesn't contain enough information, say so.

Answer:""",
    "rag_qa_with_sources": """You are a helpful assistant. Answer the question based on the context below and cite your sources.

Context:
{context}

Question: {question}

Provide a clear answer and cite the relevant source documents.

Answer:""",
    "chat": """{system_prompt}

User: {user_message}""",

}
