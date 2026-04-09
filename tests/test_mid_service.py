from kelly.md_config import TravelPolicy
from kelly.services.mid_service import apply_travel_policy


def test_apply_policy_rejects_excess_stops() -> None:
    pol = TravelPolicy(max_stops=0, direct_only=True, baggage="")
    candidates = [
        {
            "itinerary_details": [{"stops": 1, "airlineName": "X"}],
        }
    ]
    kept, rej = apply_travel_policy(candidates, pol)
    assert kept == []
    assert len(rej) == 1
