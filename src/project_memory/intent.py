"""L3: intentions. The only layer without a ground truth, so the only layer
where a human is required -- but only to *adopt* a candidate, never to author one.

A model may propose as many candidates as it likes. It may never turn its own
proposal into a commitment, and a candidate is never a premise for a decision.
"""
from .model import Intent, MemoryError, new_id, now
from .store import Store


def propose(store, text, why, refs=()):
    """Record a model-proposed candidate. Always origin='llm', always undecided."""
    intent = Intent(text=text, why=why, origin='llm', refs=tuple(refs), status='candidate')
    return store.add_intent(intent)


def decide(store, intent_id, status):
    """Operator-only. Promotes a candidate to an adopted commitment (origin becomes user)."""
    return store.decide(intent_id, status)


def adopted(store):
    return tuple(i for i in store.intents() if i.status == 'adopted')


def candidates(store):
    return tuple(i for i in store.intents() if i.status == 'candidate')


def is_commitment(intent):
    """True only for intentions a human actually adopted."""
    return intent.status == 'adopted' and intent.origin == 'user'
