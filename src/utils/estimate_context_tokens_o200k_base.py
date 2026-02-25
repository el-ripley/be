import json
import tiktoken
from typing import List, Dict, Any


def estimate_context_tokens(messages: List[Dict[str, Any]]) -> int:
    encoding = tiktoken.get_encoding("o200k_base")

    total_tokens = 0

    for msg in messages:
        msg_type = msg.get("type")

        if msg_type == "reasoning":
            # Reasoning: only encode text content from summary items
            summary = msg.get("summary", [])
            if isinstance(summary, list):
                for item in summary:
                    if isinstance(item, dict):
                        text = item.get("text", "")
                        if text:
                            # Only encode the text content, not JSON structure
                            total_tokens += len(encoding.encode(text))
            # Minimal overhead for reasoning type
            total_tokens += 1

        elif msg_type == "function_call":
            # Function call: encode name and arguments content only
            name = msg.get("name", "")
            arguments = msg.get("arguments", "{}")

            # Encode function name
            if name:
                total_tokens += len(encoding.encode(name))

            # Parse and encode only argument values, not JSON structure
            if isinstance(arguments, str):
                try:
                    args_dict = json.loads(arguments)
                except (json.JSONDecodeError, ValueError):
                    args_dict = {}
            else:
                args_dict = arguments if isinstance(arguments, dict) else {}

            # Encode argument values only (not keys or JSON structure)
            for value in args_dict.values():
                value_str = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                total_tokens += len(encoding.encode(value_str))

            # Minimal overhead
            total_tokens += 2

        elif msg_type == "function_call_output":
            # Function output: encode output content only
            output = msg.get("output", "{}")

            # Parse and encode only output values
            if isinstance(output, str):
                try:
                    output_dict = json.loads(output)
                except (json.JSONDecodeError, ValueError):
                    output_dict = {}
            else:
                output_dict = output if isinstance(output, dict) else {}

            # Encode output values only
            output_str = json.dumps(
                output_dict, separators=(",", ":"), ensure_ascii=False
            )
            total_tokens += len(encoding.encode(output_str))
            # Minimal overhead
            total_tokens += 1

        elif msg_type == "message":
            # Message: only encode content, role is minimal overhead
            content = msg.get("content", "")
            if isinstance(content, (dict, list)):
                # Use compact JSON (no spaces)
                content = json.dumps(content, separators=(",", ":"), ensure_ascii=False)
            else:
                content = str(content)

            # Only encode content, role adds minimal overhead
            total_tokens += len(encoding.encode(content))
            # Role overhead (very minimal, role names are short)
            total_tokens += 1

            if "name" in msg:
                total_tokens += len(encoding.encode(str(msg["name"])))

        else:
            # Simple format (no type): only encode content
            content = msg.get("content", "")
            if isinstance(content, (dict, list)):
                # Use compact JSON
                content = json.dumps(content, separators=(",", ":"), ensure_ascii=False)
            else:
                content = str(content)

            # Only encode content, role is minimal
            total_tokens += len(encoding.encode(content))
            # Role overhead
            total_tokens += 1

            if "name" in msg:
                total_tokens += len(encoding.encode(str(msg["name"])))

    return total_tokens
