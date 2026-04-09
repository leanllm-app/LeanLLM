"""Quick smoke test — requires a valid OPENAI_API_KEY in the environment."""

from leanllm import LeanLLM
import os

def main():
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("Set OPENAI_API_KEY to run this test.")
        return

    client = LeanLLM(api_key=api_key)

    response = client.chat(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say hello in one sentence."}],
        labels={"team": "backend", "feature": "onboarding"},
    )

    print("\n--- Response ---")
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
