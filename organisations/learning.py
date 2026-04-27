"""
Learning engine that processes user feedback to improve extraction accuracy over time.
"""


class LearningEngine:
    """
    Reads ExtractionFeedback records and updates ExtractionLearningModel so the
    extractor can use them on the next run.

    What is learned:
    - Sentiment corrections → matched keywords are added to positive/negative word lists
    - Section corrections → incorrectly detected sections are mapped to the correct ones
    - Accuracy rate → tracks what fraction of extractions are confirmed correct
    """

    def update_from_feedback(self, organisation) -> None:
        """Recompute and persist learned parameters for the given organisation."""
        from .models import ExtractionFeedback, ExtractionLearningModel

        all_fb = ExtractionFeedback.objects.filter(
            article__organisation=organisation
        ).select_related('article')

        total = all_fb.count()
        correct = all_fb.filter(verdict='correct').count()

        positive_words = []
        negative_words = []
        section_corrections = {}

        for fb in all_fb.exclude(verdict='correct'):
            article = fb.article
            matched = [k.strip() for k in (article.keywords_matched or '').split(',') if k.strip()]

            # Sentiment: associate matched keywords with the corrected sentiment
            if fb.corrected_sentiment and fb.corrected_sentiment != article.sentiment:
                if fb.corrected_sentiment == 'Positive':
                    positive_words.extend(matched)
                elif fb.corrected_sentiment == 'Negative':
                    negative_words.extend(matched)

            # Section: map the wrongly detected section → corrected section
            if fb.corrected_section and fb.corrected_section != article.section:
                section_corrections[article.section] = fb.corrected_section

        model, _ = ExtractionLearningModel.objects.get_or_create(organisation=organisation)
        model.positive_words = list(set(positive_words))
        model.negative_words = list(set(negative_words))
        model.section_corrections = section_corrections
        model.total_feedback = total
        model.correct_count = correct
        model.false_positive_rate = round((total - correct) / total, 3) if total > 0 else 0.0
        model.save()

    def get_learned_params(self, organisation) -> dict:
        """Return the stored learned parameters for an organisation."""
        from .models import ExtractionLearningModel
        try:
            m = ExtractionLearningModel.objects.get(organisation=organisation)
            return {
                'positive_words': m.positive_words or [],
                'negative_words': m.negative_words or [],
                'section_corrections': m.section_corrections or {},
            }
        except ExtractionLearningModel.DoesNotExist:
            return {'positive_words': [], 'negative_words': [], 'section_corrections': {}}
