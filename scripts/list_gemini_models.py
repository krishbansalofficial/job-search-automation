"""List Gemini models this API key can call for text generation.

Listing models does not generate content, so it does not use the scoring
request budget. Run it from the "Gemini models" workflow to pick fallbacks.
"""
from score_and_tailor import _configure


def main():
    client = _configure()
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" in actions:
            print(model.name.removeprefix("models/"))


if __name__ == "__main__":
    main()
