"""
Tests for the AI Signal Detector module.
"""
import pytest
from src.ai_signal_detector import compute_ai_exposure


class TestComputeAiExposure:
    def test_heavy_ai_jd_scores_high(self):
        """JD with many AI/LLM/GenAI terms should score > 70."""
        jd = (
            "We are building AI agents using LLMs and RAG pipelines. "
            "Experience with LangChain, OpenAI, prompt engineering, "
            "vector databases, and Hugging Face required. "
            "Generative AI experience is a must. "
            "You'll work on AI automation and agentic AI workflows."
        )
        result = compute_ai_exposure(jd)
        assert result["ai_exposure_score"] > 70
        assert len(result["ai_terms_found"]) > 5

    def test_generic_jd_scores_low(self):
        """Generic JD with no AI terms should score < 20."""
        jd = (
            "We need a Java developer with Spring Boot experience. "
            "Must know SQL, REST APIs, and microservices. "
            "Experience with Kubernetes and Docker preferred."
        )
        result = compute_ai_exposure(jd)
        assert result["ai_exposure_score"] < 20

    def test_empty_jd_scores_zero(self):
        result = compute_ai_exposure("")
        assert result["ai_exposure_score"] == 0.0
        assert result["ai_terms_found"] == []
        assert result["ai_term_count"] == 0

    def test_none_jd_scores_zero(self):
        result = compute_ai_exposure(None)
        assert result["ai_exposure_score"] == 0.0

    def test_score_is_bounded(self):
        # Even with tons of AI terms, should be <= 100
        jd = " ".join([
            "AI ML GenAI LLM RAG AI agents agentic AI OpenAI Claude Gemini "
            "LangChain LlamaIndex Hugging Face embeddings vector database "
            "prompt engineering NLP computer vision deep learning transformer "
            "GPT MLOps AI-powered AI-driven generative AI fine-tuning"
        ] * 5)
        result = compute_ai_exposure(jd)
        assert 0 <= result["ai_exposure_score"] <= 100

    def test_moderate_ai_jd_scores_middle(self):
        """JD mentioning Python/ML but not cutting-edge AI should score moderate."""
        jd = (
            "Looking for a Data Scientist with machine learning experience. "
            "Should know NLP and deep learning basics. "
            "Python and scikit-learn required."
        )
        result = compute_ai_exposure(jd)
        assert 20 <= result["ai_exposure_score"] <= 70

    def test_returns_found_terms(self):
        jd = "We use LangChain and RAG for our AI product."
        result = compute_ai_exposure(jd)
        assert "langchain" in result["ai_terms_found"]
        assert "rag" in result["ai_terms_found"]

    def test_case_insensitive(self):
        result1 = compute_ai_exposure("GenAI engineer wanted")
        result2 = compute_ai_exposure("genai ENGINEER WANTED")
        # Both should detect GenAI
        assert result1["ai_exposure_score"] > 0
        assert result2["ai_exposure_score"] > 0

    def test_single_ai_mention_nonzero(self):
        """Even a single 'AI' mention should produce a non-zero score."""
        result = compute_ai_exposure("We are an AI company")
        assert result["ai_exposure_score"] > 0
