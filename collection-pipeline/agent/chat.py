from openai import OpenAI
import os
from dotenv import load_dotenv
import json

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def chat(messages: list[dict]) -> str:
    response = client.chat.completions.create(model="gpt-5-mini", messages=messages)
    return response.choices[0].message.content


def chat_with_issue(issue_id: str, project_name: str):
    print(f"Processing {issue_id}...", end=" ")
    system_prompt = """
    You are a coding assistant that can help with coding tasks. 
    You will be given a bug issue report and you will need to select the code blocks most likely to be the root cause of the bug issue.
    The code blocks should be a direct selection of the code blocks present without changing the line numbers.
    All you need to do is to select the code blocks that are most likely to be the root cause of the bug issue.
    be selective in your selection, only select the code blocks that are most likely to be the root cause of the bug issue.
    output the code blocks in a JSON format.
    <json_schema>
    {
        "code_blocks": [
            {
                "file_path": "path/to/file.py",
                "start_line": 620,
                "end_line": 682,
                "class_func": "QuorumCnxManager.Listener.run",
                "reasoning": "reasoning for why this code block is relevant to the bug issue report"
            }
        ]
    }
    </json_schema>
    """

    with open(f"output/prompts/{project_name}/{issue_id}.json", "r") as f:
        prompt = json.load(f)
        report = prompt["title"] + "\n" + prompt["description"]
        function_blocks = prompt["blocks"]

    with open(f"output/diffs/{project_name}/{issue_id}.diff", "r") as f:
        diff = f.read()
        user_prompt = f"""
        The issue is:
        {report}
        The code changes are as follows:
        {diff}
        function blocks where the code changes are made:
        {function_blocks}
        Please select the code blocks that are relevant to the bug issue report.
        """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    os.makedirs(f"output/agent/{project_name}", exist_ok=True)
    os.makedirs(f"output/agent/{project_name}/chat", exist_ok=True)
    with open(f"output/agent/{project_name}/chat/{issue_id}.json", "w") as f:
        f.write(chat(messages))


if __name__ == "__main__":
    for project_name in os.listdir("output/diffs"):
        if project_name == "etcd":
            continue
        for root, dirs, files in os.walk(f"output/diffs/{project_name}"):
            for file in files:
                chat_with_issue(file.replace(".diff", ""), project_name)
    # chat_with_issue("ZOOKEEPER-2623", "zookeeper")
