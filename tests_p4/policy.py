"""Smoke preflight on the actual runtime YAML consumed by LiteLLM."""


def check_no_retries_or_fallbacks(config: dict) -> None:
    """Fail closed if either configuration layer can replay a model request."""
    for section, fields in (
        ("litellm_settings", ("fallbacks", "default_fallbacks")),
        (
            "router_settings",
            ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks"),
        ),
    ):
        settings = config[section]
        if type(settings.get("num_retries")) is not int or settings["num_retries"] != 0:
            raise ValueError("smoke_retries_must_be_zero")
        if any(settings.get(key) != [] for key in fields):
            raise ValueError("smoke_fallbacks_must_be_empty")
        if any(
            settings.get(key)
            for key in ("retry_policy", "model_group_retry_policy", "default_fallbacks")
        ):
            raise ValueError("smoke_replay_policy_forbidden")
    for model in config["model_list"]:
        params = model["litellm_params"]
        if any(params.get(k) for k in ("num_retries", "max_retries", "fallbacks")):
            raise ValueError("smoke_model_overrides_forbidden")
