import json
from typing import List, Optional

from presidio_analyzer import (
    RecognizerRegistry,
    RecognizerResult,
    EntityRecognizer,
    PresidioLogger,
)
from presidio_analyzer.app_tracer import AppTracer
from presidio_analyzer.nlp_engine import NLP_ENGINES, NlpEngine

logger = PresidioLogger("presidio-analyzer")


class AnalyzerEngine:
    """
    Entry point for Presidio Analyzer.

    Orchestrating the detection of PII entities and all related logic.

    :param registry: instance of type RecognizerRegistry
    :param nlp_engine: instance of type NlpEngine
    (for example SpacyNlpEngine)
    :param app_tracer: instance of type AppTracer, used to trace the logic
    used during each request for interpretability reasons.
    :param enable_trace_pii: bool,
    defines whether PII values should be traced or not.
    :param default_score_threshold: Minimum confidence value
    for detected entities to be returned
    """

    def __init__(
        self,
        registry: RecognizerRegistry = None,
        nlp_engine: NlpEngine = None,
        app_tracer: AppTracer = None,
        enable_trace_pii: bool = False,
        default_score_threshold: float = 0,
        default_language: str = "en",
    ):

        if not nlp_engine:
            logger.info(
                "nlp_engine not provided. Creating new " "SpacyNlpEngine instance"
            )
            nlp_engine = NLP_ENGINES["spacy"]()
        if not registry:
            logger.info(
                "Recognizer registry not provided. "
                "Creating default RecognizerRegistry instance"
            )
            registry = RecognizerRegistry()
        if not app_tracer:
            app_tracer = AppTracer()
        self.app_tracer = app_tracer

        self.default_language = default_language

        self.nlp_engine = nlp_engine
        self.registry = registry

        # load all recognizers
        if not registry.recognizers:
            registry.load_predefined_recognizers()

        self.enable_trace_pii = enable_trace_pii
        self.default_score_threshold = default_score_threshold

    def get_recognizers(self, language: Optional[str] = None) -> List[EntityRecognizer]:
        """
        Return a list of PII recognizers currently loaded.

        :param language: Return the recognizers supporting a given language.
        :return: List of [Recognizer] as a RecognizersAllResponse
        """
        if not language:
            language = self.default_language
        logger.info(f"Fetching all recognizers for language {language}")
        recognizers = self.registry.get_recognizers(language=language, all_fields=True)
        return recognizers

    def get_supported_entities(self, language: Optional[str] = None) -> List[str]:
        """
        Return a list of the entities that can be detected.

        :param language: Return only entities supported in a specific language.
        :return: List of entity names
        """
        recognizers = self.get_recognizers(language=language)
        supported_entities = []
        for recognizer in recognizers:
            supported_entities.extend(recognizer.get_supported_entities())

        return list(set(supported_entities))

    def analyze(
        self,
        text: str,
        language: str,
        entities: Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
        score_threshold: Optional[float] = None,
        trace: Optional[bool] = False,
        remove_interpretability_response: Optional[bool] = False,
    ) -> List[RecognizerResult]:
        """
        Find PII entities in text using different PII recognizers for a given language.

        :param text: the text to analyze
        :param language: the language of the text
        :param entities: List of PII entities that should be looked for in the text.
        If entities=None then all entities are looked for.
        :param correlation_id: cross call ID for this request
        :param score_threshold: A minimum value for which
        to return an identified entity
        :param trace: Should tracing of the response occur or not
        :param remove_interpretability_response:
        Should the interpretability text be returned in the response.
        :return: an array of the found entities in the text
        """
        all_fields = not entities

        recognizers = self.registry.get_recognizers(
            language=language, entities=entities, all_fields=all_fields
        )

        if all_fields:
            # Since all_fields=True, list all entities by iterating
            # over all recognizers
            entities = self.get_supported_entities(language=language)

        # run the nlp pipeline over the given text, store the results in
        # a NlpArtifacts instance
        nlp_artifacts = self.nlp_engine.process_text(text, language)

        if self.enable_trace_pii and trace:
            self.app_tracer.trace(
                correlation_id, "nlp artifacts:" + nlp_artifacts.to_json()
            )

        results = []
        for recognizer in recognizers:
            # Lazy loading of the relevant recognizers
            if not recognizer.is_loaded:
                recognizer.load()
                recognizer.is_loaded = True

            # analyze using the current recognizer and append the results
            current_results = recognizer.analyze(
                text=text, entities=entities, nlp_artifacts=nlp_artifacts
            )
            if current_results:
                results.extend(current_results)

        if trace:
            self.app_tracer.trace(
                correlation_id,
                json.dumps([str(result.to_dict()) for result in results]),
            )

        # Remove duplicates or low score results
        results = EntityRecognizer.remove_duplicates(results)
        results = self.__remove_low_scores(results, score_threshold)
        results = self.__remove_analysis_explanation(
            results, remove_interpretability_response
        )

        return results

    def __remove_low_scores(
        self, results: List[RecognizerResult], score_threshold: float = None
    ) -> List[RecognizerResult]:
        """
        Remove results for which the confidence is lower than the threshold.

        :param results: List of RecognizerResult
        :param score_threshold: float value for minimum possible confidence
        :return: List[RecognizerResult]
        """
        if score_threshold is None:
            score_threshold = self.default_score_threshold

        new_results = [result for result in results if result.score >= score_threshold]
        return new_results

    def __remove_analysis_explanation(
        self,
        results: List[RecognizerResult],
        remove_interpretability_response: bool = None,
    ) -> List[RecognizerResult]:
        """Remove interpretability from response."""
        if not remove_interpretability_response:
            return results

        new_results = []
        for recognizer in results:
            recognizer.analysis_explanation = None
            new_results.append(recognizer)

        return new_results
