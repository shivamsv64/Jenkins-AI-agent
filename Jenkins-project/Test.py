import json
import os
import boto3
from botocore.exceptions import ClientError

# Optional: only needed if you want to use Anthropic's dedicated SDK.
# from anthropic import AnthropicBedrock


def test_ai_model():
    region_name = os.getenv("AWS_REGION", "us-east-1")
    print(f"Initializing Bedrock client in {region_name} region")
    client = boto3.client("bedrock-runtime", region_name=region_name)

    # Bedrock model identifier for the Nova request format.
    model_id = "amazon.nova-pro-v1:0"

    # Example prompt (can be replaced with user input later).
    user_prompt = input("Enter your prompt for the AI : ")

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "text": user_prompt
                }
            ]
        }
    ]

    print(f"Sending request to Bedrock with prompt: {model_id}")

    try:
        response = client.converse(
            modelId=model_id,
            messages=messages,
            inferenceConfig={
                "maxTokens": 2048,
                "temperature": 0.7,
            }
        )
        output_text = response["output"]["message"]["content"][0]["text"]

        print("\nSuccessfully received response from Bedrock:")
        print(output_text)
        print("\nTest completed successfully.")
    except ClientError as e:
        print(f"An error occurred: {e.response['Error']['Message']}")
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")

if __name__ == "__main__":
    test_ai_model()
