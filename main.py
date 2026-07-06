import asyncio
import json
import os
from pathlib import Path
from typing import Literal, Any
from uuid import uuid4

from dotenv import load_dotenv
from agents import Agent, Runner, function_tool


load_dotenv()

TASKS_FILE = Path("tasks.json")


# -----------------------------
# Local storage helpers
# -----------------------------
def load_tasks() -> list[dict]:
    if not TASKS_FILE.exists():
        TASKS_FILE.write_text("[]")

    try:
        return json.loads(TASKS_FILE.read_text())
    except json.JSONDecodeError:
        return []


def save_tasks(tasks: list[dict]) -> None:
    TASKS_FILE.write_text(json.dumps(tasks, indent=2))


def find_task(tasks: list[dict], task_ref: str) -> dict | None:
    """
    Finds a task using:
    1. Exact task id
    2. Exact title match
    3. Partial title match
    """

    task_ref_lower = task_ref.lower().strip()

    # 1. Match by id
    for task in tasks:
        if task["id"] == task_ref:
            return task

    # 2. Exact title match
    for task in tasks:
        if task["title"].lower() == task_ref_lower:
            return task

    # 3. Partial title match
    for task in tasks:
        if task_ref_lower in task["title"].lower():
            return task

    return None


# -----------------------------
# Agent tools
# -----------------------------
@function_tool
def add_task(task_title: str) -> str:
    """
    Add a new task to the user's task list.

    Args:
        task_title: The title or description of the task to add.
    """

    tasks = load_tasks()

    new_task = {
        "id": str(uuid4())[:8],
        "title": task_title.strip(),
        "status": "pending",
    }

    tasks.append(new_task)
    save_tasks(tasks)

    return f'Task added: "{new_task["title"]}" with id {new_task["id"]}.'


@function_tool
def list_tasks(status: Literal["all", "pending", "done"] = "pending") -> str:
    """
    List the user's tasks.

    Args:
        status: Which tasks to show. Use "pending", "done", or "all".
    """

    tasks = load_tasks()

    if not tasks:
        return "There are no tasks yet."

    if status != "all":
        tasks = [task for task in tasks if task["status"] == status]

    if not tasks:
        return f"There are no {status} tasks."

    lines = []

    for index, task in enumerate(tasks, start=1):
        status_icon = "✅" if task["status"] == "done" else "⏳"
        lines.append(
            f'{index}. {status_icon} {task["title"]} '
            f'(id: {task["id"]}, status: {task["status"]})'
        )

    return "\n".join(lines)


@function_tool
def complete_task(task_ref: str) -> str:
    """
    Mark a task as completed.

    Args:
        task_ref: The task id, exact title, or partial title of the task.
    """

    tasks = load_tasks()

    task = find_task(tasks, task_ref)

    if task is None:
        return f'No task found matching "{task_ref}".'

    if task["status"] == "done":
        return f'Task "{task["title"]}" is already completed.'

    task["status"] = "done"
    save_tasks(tasks)

    return f'Done. Marked "{task["title"]}" as completed.'


@function_tool
def reopen_task(task_ref: str) -> str:
    """
    Reopen a completed task and mark it as pending again.

    Args:
        task_ref: The task id, exact title, or partial title of the task.
    """

    tasks = load_tasks()

    task = find_task(tasks, task_ref)

    if task is None:
        return f'No task found matching "{task_ref}".'

    if task["status"] == "pending":
        return f'Task "{task["title"]}" is already pending.'

    task["status"] = "pending"
    save_tasks(tasks)

    return f'Reopened "{task["title"]}". It is pending again.'


# -----------------------------
# Agent definition
# -----------------------------
personal_agent = Agent(
    name="Personal Task Agent",
    instructions="""
You are a helpful personal productivity assistant.

You help the user manage a simple task list.

Use the available tools when the user wants to:
- add a task
- list tasks
- see pending tasks
- see completed tasks
- mark a task as done
- reopen a task

Important behavior:
- If the user asks a normal question that does not require task management, answer normally.
- If the user asks to add multiple tasks, call add_task once for each task.
- If the user says something like "mark it done", use the conversation context to understand what "it" refers to.
- Keep responses short and natural.
""",
    tools=[
        add_task,
        list_tasks,
        complete_task,
        reopen_task,
    ],
)


# -----------------------------
# Tool trace helpers
# -----------------------------
def get_raw_value(raw_item: Any, key: str, default: Any = None) -> Any:
    """
    raw_item can be a dict or an SDK/Pydantic object.
    This helper safely reads values from both.
    """

    if isinstance(raw_item, dict):
        return raw_item.get(key, default)

    return getattr(raw_item, key, default)


def pretty_print_value(value: Any) -> str:
    """
    Pretty print dict/list values.
    Keep normal strings readable.
    """

    if value is None:
        return "None"

    if isinstance(value, str):
        # Try to parse JSON strings like '{"task_title": "..."}'
        try:
            parsed = json.loads(value)
            return json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            return value

    try:
        return json.dumps(value, indent=2, default=str)
    except TypeError:
        return str(value)


def print_tool_trace(result) -> None:
    """
    Prints:
    - tool name
    - tool input
    - tool output

    Uses result.new_items from the Agents SDK.
    """

    tool_calls_by_id = {}
    tool_was_used = False

    for item in result.new_items:
        item_type = getattr(item, "type", None)

        if item_type == "tool_call_item":
            tool_was_used = True

            raw_item = getattr(item, "raw_item", None)

            tool_name = (
                getattr(item, "tool_name", None)
                or get_raw_value(raw_item, "name", "unknown_tool")
            )

            call_id = (
                getattr(item, "call_id", None)
                or get_raw_value(raw_item, "call_id")
                or get_raw_value(raw_item, "id")
            )

            arguments = get_raw_value(raw_item, "arguments", "{}")

            tool_calls_by_id[call_id] = tool_name

            print("\n🛠️  Tool Used")
            print(f"Name: {tool_name}")
            print("Input:")
            print(pretty_print_value(arguments))

        elif item_type == "tool_call_output_item":
            raw_item = getattr(item, "raw_item", None)

            call_id = (
                getattr(item, "call_id", None)
                or get_raw_value(raw_item, "call_id")
                or get_raw_value(raw_item, "id")
            )

            tool_name = tool_calls_by_id.get(call_id, "unknown_tool")
            output = getattr(item, "output", None)

            print("\n📤 Tool Output")
            print(f"Name: {tool_name}")
            print("Output:")
            print(pretty_print_value(output))

    if not tool_was_used:
        print("\n🛠️  Tool Used: None")


# -----------------------------
# CLI chat loop
# -----------------------------
async def main():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to your .env file.")

    print("\nPersonal Task Agent")
    print("Type 'exit' or 'quit' to stop.\n")

    conversation_history = []

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in {"exit", "quit"}:
            print("Agent: Bye!")
            break

        if not user_input:
            continue

        agent_input = conversation_history + [
            {
                "role": "user",
                "content": user_input,
            }
        ]

        result = await Runner.run(
            personal_agent,
            agent_input,
        )

        print_tool_trace(result)

        print(f"\n🤖 Agent Final Answer:\n{result.final_output}\n")

        conversation_history = result.to_input_list()


if __name__ == "__main__":
    asyncio.run(main())