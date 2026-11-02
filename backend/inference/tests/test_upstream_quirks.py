from backend.inference import upstream


def test_opencode_gets_client_name_and_stable_session_per_conversation():
    convo = {
        "model": "deepseek-v4.1-flash",
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
    }
    longer = {
        **convo,
        "messages": [*convo["messages"], {"role": "assistant", "content": "a"}, {"role": "user", "content": "go on"}],
    }
    other = {**convo, "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "another app"}]}

    def session(body):
        headers = {"User-Agent": "llmlab-executor"}
        assert upstream._provider_quirks("opencode.ai", body, headers) is body
        assert headers["User-Agent"] == upstream.OPENCODE_USER_AGENT
        return headers["x-opencode-session"]

    assert session(convo) == session(longer)
    assert session(convo) != session(other)
    assert session(None).startswith("llmlab-")


def test_other_hosts_get_no_opencode_headers():
    headers = {}
    upstream._provider_quirks("api.example.com", {"model": "m"}, headers)
    assert headers == {}
