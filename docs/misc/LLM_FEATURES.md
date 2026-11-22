## New Features Added

### 1. Streaming Response Support ✅

Both `OpenAIWrapper` and `HuggingFaceWrapper` now support streaming:

```python
from src.llm.openai_wrapper import OpenAIWrapper
from src.llm.config import OpenAIConfig

config = OpenAIConfig(model="gpt-3.5-turbo")
wrapper = OpenAIWrapper(config)

# Stream response token by token
for chunk in wrapper.stream("Explain RAG"):
    print(chunk, end="", flush=True)
```

**Benefits:**
- Lower perceived latency (user sees output immediately)
- Better UX for long responses
- Can process chunks as they arrive

### 2. Batch Processing ✅

Process multiple prompts in parallel with `BatchProcessor`:

```python
from src.llm.batch import BatchProcessor

processor = BatchProcessor(wrapper, max_workers=5)

prompts = ["Question 1?", "Question 2?", "Question 3?"]

# Process with progress callback
def on_complete(idx, result):
    print(f"Completed {idx + 1}")

results = processor.process_batch(prompts, callback=on_complete)

# Or use simple retry helper
from src.llm.batch import batch_with_retry
results = batch_with_retry(wrapper, prompts, max_retries=3)

# Process in chunks
from src.llm.batch import chunk_prompts
for chunk in chunk_prompts(prompts, chunk_size=10):
    results = processor.process_batch(chunk)
```

**Features:**
- Parallel execution with ThreadPoolExecutor
- Progress callbacks
- Error handling (fail-fast or collect all)
- Automatic retry logic
- Streaming batch support

### 4. RAG Pipeline Example ✅

Complete end-to-end RAG implementation in `examples/rag_pipeline_demo.py`:

```python
# Initialize components
vector_store = SimpleVectorStore()
vector_store.add_documents(documents)

llm = OpenAIWrapper(config)
prompts = PromptManager()
rag = RAGPipeline(llm, vector_store, prompts)

# Query with retrieval + generation
answer = rag.query("What is RAG?", top_k=3, stream=True)
```

**Pipeline stages:**
1. Document ingestion & chunking
2. Embedding generation (sentence-transformers)
3. Vector indexing (FAISS)
4. Similarity search for retrieval
5. Prompt formatting with context
6. LLM generation with sources

## Installation

```cmd
pip install -r requirements.txt
```

New dependencies:
- `faiss-cpu` - Vector similarity search
- `sentence-transformers` - Embedding models


## API Reference

### Streaming

```python
# Both wrappers support streaming
for chunk in wrapper.stream(prompt, temperature=0.7):
    print(chunk, end="")
```

### Batch Processing

```python
processor = BatchProcessor(
    wrapper,
    max_workers=5,      # Parallel threads
    fail_fast=False,    # Continue on errors
)

results = processor.process_batch(
    prompts,
    callback=on_complete,
    temperature=0.7,    # Kwargs passed to generate()
)
```

## Performance Tips

1. **Batch processing**: Use `max_workers` based on API rate limits
2. **Streaming**: Best for interactive UX, not for bulk processing
3. **Rate limits**: Set `rate_limit_rpm` and `rate_limit_tpm` in config
4. **Caching**: Reuse `CostTracker` across wrappers for shared limits
5. **Templates**: Load once at startup, reuse for all requests

## Testing

```cmd
# Unit tests (no API keys needed)
pytest tests/test_llm.py -v

# Integration tests (requires API keys)
pytest tests/test_llm.py --run-integration -v
```

## Cost Tracking

All operations automatically log costs to `logs/llm_costs.jsonl`:

```python
summary = wrapper.get_cost_summary()
# {
#   'total_calls': 10,
#   'total_tokens': 5000,
#   'total_cost_usd': 0.0125,
#   'per_model': {...}
# }
```

## Next Steps

- Add async/await support for concurrent streaming
- Add prompt versioning and A/B testing
- Add semantic caching for repeated queries
- Add multi-modal RAG (images, tables)
- Add evaluation metrics for RAG quality
