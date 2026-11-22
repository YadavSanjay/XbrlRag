
import json
from pathlib import Path
from typing import List

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BaseMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.test_case import LLMTestCase
from src import BENCHMARKS_DIR, PROJECT_ROOT
from src.logging_config import get_logger
from src.pipelines.sec10k.retrieval_pipeline import RetrievalPipeline, RetrievalType

logger = get_logger(__name__)


DEFAULT_MODEL = "gpt-5-mini"

class Sec10KRagStandardEval:

    def __init__(self):
        self.retrieval_pipeline = RetrievalPipeline()
        self.metrics = self._get_metrics()
        self.sec10k_benchmark_path = BENCHMARKS_DIR / "financebench_inspired_questions.json"

    def evaluate(self):
        benchmark_data = self.load_benchmark(self.sec10k_benchmark_path, 'google')
        benchmark_data = {k:v for k,v in benchmark_data.items() if k in ['google_008']}
        # dict['questionId', dict['retrieval_type', dict['metrics_name', metric_score]]
        evaluation_results: dict[str, dict[str,dict[str,float]]] = {}
        for question_id, (input_query, expected_output) in benchmark_data.items():
            evaluation_results[question_id] = {}
            #retrieval_types = [RetrievalType.BASIC, RetrievalType.SUBQUERY, RetrievalType.HYDE]
            retrieval_types = [RetrievalType.HYDE]
            for retrieval_type in retrieval_types:
                rag_response = self.retrieval_pipeline.generate(input_query, retrieval_type, top_k=10)
                actual_output = rag_response.answer_summary
                retrieval_context = [chunk_text for chunk_id,chunk_text in rag_response.retrieved_chunks]
                # Evaluate metrics
                evaluation_results[question_id][retrieval_type.name] = {}
                for metric_name, metric in self.metrics.items():
                    test_case = self._getTestCase(input_query, actual_output, expected_output, retrieval_context)
                    metric.measure(test_case)
                    score, reason = metric.score, metric.reason
                    evaluation_results[question_id][retrieval_type.name][metric_name] = score
                    logger.info(f"eval_result:{question_id}:{retrieval_type.name}:{metric_name}:{score}:{reason}")

        return evaluation_results


    def load_benchmark(self, benchmark_path: Path, company=None) -> dict[str, list[str]]:
        test_cases: dict[str, list[str]] = {}
        with open(benchmark_path, encoding="utf-8") as file:
            data = json.load(file)
            for item in data:
                question_id = item.get("question_id", "")
                if (company is not None and company in question_id) or (company is None):
                    input_query = item.get("question")
                    expected_output = item.get("answer")
                    test_cases[question_id] = [input_query, expected_output]
        return test_cases


    def _getTestCase(self, input_query, actual_output, expected_output, retrieval_context)-> LLMTestCase:
        test_case = LLMTestCase(
            input=input_query,
            actual_output=actual_output,
            expected_output=expected_output,
            retrieval_context=retrieval_context,
        )
        return test_case



    def _get_metrics(self) -> dict[str, BaseMetric]:
        metrics = {}
        metrics["answer_relevancy"] = AnswerRelevancyMetric(
            threshold=0.5,
            model=DEFAULT_MODEL,
            include_reason=True,
            strict_mode=False,
            async_mode=False,
            verbose_mode=True,
        )
        metrics["faithfulness"] = FaithfulnessMetric(
            threshold=0.5,
            model=DEFAULT_MODEL,
            include_reason=True,
            strict_mode=False,
            async_mode=False,
            verbose_mode=True,
            penalize_ambiguous_claims=True,
        )
        metrics["contextual_precision"] = ContextualPrecisionMetric(
            threshold=0.5,
            model=DEFAULT_MODEL,
            include_reason=True,
            strict_mode=False,
            async_mode=False,
            verbose_mode=True,
        )
        metrics["contextual_recall"] = ContextualRecallMetric(
            threshold=0.5,
            model=DEFAULT_MODEL,
            include_reason=True,
            strict_mode=False,
            async_mode=False,
            verbose_mode=True,
        )
        metrics["contextual_relevancy"] = ContextualRelevancyMetric(
            threshold=0.5,
            model=DEFAULT_MODEL,
            include_reason=True,
            strict_mode=False,
            async_mode=False,
            verbose_mode=True,
        )

        return metrics

if __name__ == "__main__":
    evaluator = Sec10KRagStandardEval()
    results = evaluator.evaluate()
    print("----------eval finished----------")
