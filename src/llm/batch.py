"""Batch processing helpers for LLM calls."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.llm.base import BaseLLMWrapper
from src.logging_config import get_logger

logger = get_logger(__name__)


# TODO : Should be moved to/invoked from Pipeline module

class BatchProcessor:
    """Helper for processing multiple prompts in batches.

    Supports parallel execution with thread pools and progress tracking.

    Example:
        >>> processor = BatchProcessor(wrapper, max_workers=5)
        >>> results = processor.process_batch(prompts)
    """

    def __init__(
        self,
        wrapper: BaseLLMWrapper,
        max_workers: int = 5,
        fail_fast: bool = False,
    ):
        """Initialize batch processor.

        Args:
            wrapper: LLM wrapper to use for generation
            max_workers: Maximum parallel workers
            fail_fast: If True, stop on first error; if False, continue and collect errors
        """
        self.wrapper = wrapper
        self.max_workers = max_workers
        self.fail_fast = fail_fast

    def process_batch(
        self,
        prompts: list[str],
        callback: Callable[[int, str], None] | None = None,
        **generation_kwargs: Any,
    ) -> list[str | Exception]:
        """Process a batch of prompts in parallel.

        Args:
            prompts: List of prompt strings
            callback: Optional callback(index, result) called after each completion
            **generation_kwargs: Kwargs passed to wrapper.generate()

        Returns:
            List of results (strings) or exceptions if errors occurred
        """
        results: list[str | Exception] = [None] * len(prompts)  # type: ignore

        logger.info(f"Processing batch of {len(prompts)} prompts with {self.max_workers} workers")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_index = {
                executor.submit(self._process_single, prompt, **generation_kwargs): i
                for i, prompt in enumerate(prompts)
            }

            # Collect results as they complete
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    result = future.result()
                    results[idx] = result

                    if callback:
                        callback(idx, result)

                    logger.debug(f"Completed prompt {idx + 1}/{len(prompts)}")

                except Exception as e:
                    logger.error(f"Error processing prompt {idx}: {e}")
                    results[idx] = e

                    if self.fail_fast:
                        logger.error("Fail-fast enabled, cancelling remaining tasks")
                        for f in future_to_index:
                            f.cancel()
                        break

        # Count successes and failures
        successes = sum(1 for r in results if not isinstance(r, Exception))
        failures = len(results) - successes

        logger.info(f"Batch complete: {successes} succeeded, {failures} failed")
        return results

    def _process_single(self, prompt: str, **kwargs: Any) -> str:
        """Process a single prompt (internal helper)."""
        return self.wrapper.generate(prompt, **kwargs)

    def process_batch_streaming(
        self,
        prompts: list[str],
        **generation_kwargs: Any,
    ) -> Iterator[tuple[int, str]]:
        """Process batch with streaming, yielding (index, chunk) as they arrive.

        Args:
            prompts: List of prompt strings
            **generation_kwargs: Kwargs passed to wrapper.stream()

        Yields:
            Tuples of (prompt_index, text_chunk)
        """
        logger.info(f"Processing batch of {len(prompts)} prompts with streaming")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all streaming tasks
            future_to_index = {
                executor.submit(self._collect_stream, prompt, **generation_kwargs): i
                for i, prompt in enumerate(prompts)
            }

            # Yield results as they complete
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    full_response = future.result()
                    yield (idx, full_response)
                    logger.debug(f"Completed streaming prompt {idx + 1}/{len(prompts)}")
                except Exception as e:
                    logger.error(f"Error streaming prompt {idx}: {e}")
                    yield (idx, f"ERROR: {e}")

    def _collect_stream(self, prompt: str, **kwargs: Any) -> str:
        """Collect full response from streaming (internal helper)."""
        chunks = []
        for chunk in self.wrapper.stream(prompt, **kwargs):
            chunks.append(chunk)
        return "".join(chunks)


def batch_with_retry(
    wrapper: BaseLLMWrapper,
    prompts: list[str],
    max_retries: int = 3,
    **generation_kwargs: Any,
) -> list[str | Exception]:
    """Simple batch processor with per-prompt retry logic.

    Args:
        wrapper: LLM wrapper
        prompts: List of prompts
        max_retries: Max retry attempts per prompt
        **generation_kwargs: Kwargs for wrapper.generate()

    Returns:
        List of results or exceptions
    """
    results = []

    for i, prompt in enumerate(prompts):
        attempt = 0
        while attempt < max_retries:
            try:
                result = wrapper.generate(prompt, **generation_kwargs)
                results.append(result)
                logger.debug(f"Prompt {i + 1}/{len(prompts)} succeeded on attempt {attempt + 1}")
                break
            except Exception as e:
                attempt += 1
                if attempt >= max_retries:
                    logger.error(f"Prompt {i + 1}/{len(prompts)} failed after {max_retries} attempts: {e}")
                    results.append(e)
                else:
                    logger.warning(f"Prompt {i + 1}/{len(prompts)} attempt {attempt} failed, retrying: {e}")

    return results


def chunk_prompts(prompts: list[str], chunk_size: int) -> Iterator[list[str]]:
    """Split prompts into chunks for batch processing.

    Args:
        prompts: List of prompts
        chunk_size: Size of each chunk

    Yields:
        Lists of prompts of size chunk_size (last chunk may be smaller)
    """
    for i in range(0, len(prompts), chunk_size):
        yield prompts[i : i + chunk_size]
