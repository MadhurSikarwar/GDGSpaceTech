from datetime import datetime, timezone
from services.propagation.app.synthetic.generator import generate_verified_synthetic_debris
from services.propagation.app.screening.fine_filter import FineFilter

ISS_LINE1 = "1 25544U 98067A   26255.34409722  .00016717  00000+0  30154-3 0  9993"
ISS_LINE2 = "2 25544  51.6416 230.1254 0006241 120.4512 245.6721 15.49812345421508"


def test_synthetic_debris_generation_and_conjunction():
    iss_obj = {
        "catalog_id": "25544",
        "name": "ISS",
        "object_type": "SATELLITE",
        "tle_line_1": ISS_LINE1,
        "tle_line_2": ISS_LINE2,
        "source": "CelesTrak"
    }

    synth_obj = generate_verified_synthetic_debris(iss_obj, tca_offset_minutes=45.0)

    # Verify labelling and ID rules. The numeric suffix is randomized per call
    # (see generator.py's synth_num, salted with random.randint to avoid
    # collisions across repeated injections) so only the format is checked,
    # not an exact value.
    assert synth_obj["catalog_id"].startswith("SYNTHETIC-")
    assert synth_obj["catalog_id"][len("SYNTHETIC-"):].isdigit()
    assert synth_obj["object_type"] == "SYNTHETIC_DEBRIS"
    # Display name is "DEB-<target clean name>-<same numeric suffix as catalog_id>"
    # (generator.py) -- object_type, not the name string, carries the
    # SYNTHETIC_DEBRIS classification.
    assert synth_obj["name"].startswith("DEB-")
    assert synth_obj["name"].endswith(synth_obj["catalog_id"].split("-")[1])

    # Run fine filter and check detected close approach candidate
    fine = FineFilter(threshold_km=50.0)
    candidate = fine.compute_conjunction_candidate(iss_obj, synth_obj, horizon_minutes=90)

    assert candidate is not None
    assert candidate.primary_object == "25544"
    assert candidate.secondary_object == synth_obj["catalog_id"]
    assert candidate.closest_approach.distance_km <= 50.0
