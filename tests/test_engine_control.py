from concurrent.futures import ThreadPoolExecutor


OWNER = "desktop-test"


def setup(client):
    r = client.post("/engine/setup", json=dict(owner=OWNER, dit="a", lm="none",
                    dit_models=["a", "b"], lm_models=["none", "lm-small"]))
    assert r.status_code == 200
    client.post("/worker/register", json=dict(worker_id="gpu", backend="ace", capabilities=["generate"],
                dit_models=[dict(name="a", is_default=True)], lm_models=[dict(name="none")]))
    applied = client.post("/engine/apply", json=dict(dit="a", lm="none")).json()
    assert result(client, applied["revision"], "ready").status_code == 200


def result(client, revision, status, **extra):
    return client.post("/engine/result", json=dict(owner=OWNER, revision=revision, status=status, **extra))


def test_apply_is_explicit_and_rejected_while_busy(client):
    setup(client)
    before = client.get("/engine").json()
    job = client.post("/generate", json=dict(prompt="piano", dit_model="a")).json()["job_id"]
    assert client.post("/engine/apply", json=dict(dit="b", lm="lm-small")).status_code == 409
    assert client.get("/engine").json() == before
    assert client.post("/engine/poll", json=dict(owner=OWNER)).json() is None
    client.post("/worker/claim", json=dict(worker_id="gpu", types=["generate"]))
    assert client.post("/engine/apply", json=dict(dit="b", lm="lm-small")).status_code == 409
    client.post(f"/worker/jobs/{job}/fail", json=dict(error="test completed"))
    accepted = client.post("/engine/apply", json=dict(dit="b", lm="lm-small"))
    assert accepted.status_code == 200 and accepted.json()["status"] == "loading"
    assert client.post("/generate", json=dict(prompt="piano", dit_model="a")).status_code == 409
    assert client.post("/train", json=dict(dataset_id=1, base_model="a", style_name="test")).status_code == 409
    assert client.post("/tracks/1/autolabel").status_code == 409
    assert client.post("/worker/claim", json=dict(worker_id="gpu", types=["generate"])).status_code == 204
    assert result(client, accepted.json()["revision"], "ready").status_code == 200
    assert client.post("/generate", json=dict(prompt="piano", dit_model="a")).status_code == 409


def test_invalid_models_and_unchanged_configuration_do_not_restart(client):
    setup(client)
    state = client.get("/engine").json()
    assert client.post("/engine/apply", json=dict(dit="a", lm="none")).json() == state
    assert client.post("/engine/apply", json=dict(dit="../bad", lm="none")).status_code == 400
    assert client.post("/engine/apply", json=dict(dit="a", lm="missing")).status_code == 400
    assert client.get("/engine").json() == state


def test_one_claim_only_and_failed_load_can_be_retried(client):
    setup(client)
    state = client.post("/engine/apply", json=dict(dit="b", lm="lm-small")).json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: client.post("/engine/poll", json=dict(owner=OWNER)).json(), range(8)))
    assert sum(c is not None for c in claims) == 1
    assert result(client, state["revision"], "error", error="OOM").status_code == 200
    assert client.post("/generate", json=dict(prompt="piano", dit_model="b")).status_code == 409
    retry = client.post("/engine/apply", json=dict(dit="a", lm="none")).json()
    assert retry["revision"] > state["revision"]
    assert result(client, state["revision"], "ready").status_code == 409
    assert client.post("/engine/poll", json=dict(owner="other")).status_code == 403


def test_unmanaged_launch_retains_existing_workflow(client):
    assert client.get("/engine").json()["managed"] is False
    assert client.post("/generate", json=dict(prompt="piano", dit_model="a")).status_code == 200
    assert client.post("/engine/apply", json=dict(dit="a", lm="none")).status_code == 409


def test_setup_waits_for_explicit_first_load(client):
    state = client.post("/engine/setup", json=dict(owner=OWNER, dit="a", lm="none",
                        dit_models=["a"], lm_models=["none"])).json()
    assert state["status"] == "idle"
    assert client.post("/engine/poll", json=dict(owner=OWNER)).json() is None
    assert client.post("/generate", json=dict(prompt="piano", dit_model="a")).status_code == 409
    assert result(client, state["revision"], "ready").status_code == 409
    assert client.post("/engine/apply", json=dict(dit="a", lm="none")).json()["status"] == "loading"


def test_first_load_can_resume_compatible_preexisting_queued_jobs(client):
    job = client.post("/generate", json=dict(prompt="piano", dit_model="a", lm_model="none")).json()["job_id"]
    client.post("/engine/setup", json=dict(owner=OWNER, dit="a", lm="none", dit_models=["a", "b"], lm_models=["none"]))
    assert client.post("/engine/apply", json=dict(dit="b", lm="none")).status_code == 409
    accepted = client.post("/engine/apply", json=dict(dit="a", lm="none"))
    assert accepted.status_code == 200
    assert client.get(f"/jobs/{job}").json()["status"] == "queued"
