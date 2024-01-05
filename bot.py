import asyncio
import subprocess
import json
import os
import datetime
import openai
from openai import OpenAI
from dotenv import load_dotenv
from dotenv import dotenv_values

# Load environment variables from .env file
load_dotenv()

class AIManager:
    def __init__(self, openai_api_key, daily_budget, command_timeout, base_cycle_dir):
        self.openai_api_key = openai_api_key
        self.daily_budget = daily_budget
        self.spent_today = 0
        self.last_reset = datetime.date.today()
        self.command_executor = CommandExecutor(timeout=command_timeout)
        self.base_cycle_dir = base_cycle_dir
        self.cycle_count = self.get_latest_cycle_count()
        

    def get_latest_cycle_count(self):
        if not os.path.exists(self.base_cycle_dir):
            os.makedirs(self.base_cycle_dir)
            return 0
        cycle_dirs = [d for d in os.listdir(self.base_cycle_dir) if os.path.isdir(os.path.join(self.base_cycle_dir, d))]
        cycle_nums = [int(d.split('_')[1]) for d in cycle_dirs if d.startswith('cycle_')]
        return max(cycle_nums, default=0)

    def reset_daily_budget(self):
        if datetime.date.today() > self.last_reset:
            self.spent_today = 0
            self.last_reset = datetime.date.today()

    def can_make_api_call(self, estimated_tokens):
        input_cost_per_token = 0.01 / 1000
        output_cost_per_token = 0.03 / 1000
        total_cost_per_token = input_cost_per_token + output_cost_per_token
        estimated_cost = estimated_tokens * total_cost_per_token
        return (self.spent_today + estimated_cost) <= self.daily_budget

    async def generate_task_with_gpt4(self, dynamic_part):
        ETHKey = os.getenv('ETH_KEY')
        SOLKey = os.getenv('SOL_KEY')
        
        static_part = (
            "Your primary objective is to generate profit using any available resources. You are in a competition for survival so be daring. You are running in a script, bot.py in the cwd, that has root access on a ubuntu server. "
            "There are some seed funds in Ethereum and Solana. Port 80 and 443 are open."
            "respond in JSON format. The response should include either a 'cmd' "
            "field for server-executable commands or an 'ask' field for actions requiring human intervention. Ask for human intervention as a last resort. Also, include a 'prompt' field with "
            "instructions for the next cycle (this need not be human readable but needs to be complete as it will not have previous commands). This is your standard operating format "
            "for each cycle. Extra points for efficient use of your API and creativity. Use command 'exit' to exit to restart the bot if you have updated the script. Abide by aws rules."
        )
        envvars = dotenv_values(".env")
        #append envvars (a dict) to static_part
        static_part += "\n" + json.dumps(envvars)
        
        prompt = f"{static_part} {dynamic_part}"
        estimated_tokens = len(prompt.split()) + 100

        if self.can_make_api_call(estimated_tokens):
            
            conversation = [
                {"role": "system", "content": static_part},
                {"role": "user",
                    "content": dynamic_part }
            ]
            client = OpenAI(
                # This is the default and can be omitted
                api_key=self.openai_api_key,
            )

            model = "gpt-4-1106-preview"
            response = client.chat.completions.create(
                model=model,
                messages=conversation,
                n=1,
                stop=None,
                temperature=0.6,
                response_format={ "type": "json_object" }
            )
            try:
                print(json.dumps(response.choices[0].message.content.strip(), indent=4))
            except:
                print(response)
                
            input_cost_per_token = 0.01 / 1000
            output_cost_per_token = 0.03 / 1000
            total_cost_per_token = input_cost_per_token + output_cost_per_token
            self.spent_today += (len(response.choices[0].message.content.split()) + len(prompt.split())) * total_cost_per_token
            return response.choices[0].message.content.strip()
        else:
            return "API call limit reached for today."

    def create_cycle_directory(self):
        self.cycle_count += 1
        cycle_dir = os.path.join(self.base_cycle_dir, f"cycle_{self.cycle_count}")
        os.makedirs(cycle_dir, exist_ok=True)
        return cycle_dir

    def write_to_file(self, cycle_dir, filename, content):
        with open(os.path.join(cycle_dir, filename), 'w') as file:
            json.dump(content, file)

    async def wait_for_human_input(self, cycle_dir):
        human_input_path = os.path.join(cycle_dir, 'results.txt')
        while not os.path.exists(human_input_path):
            await asyncio.sleep(5)  # wait for 5 seconds before checking again
        with open(human_input_path, 'r') as file:
            return file.read()

    async def execute_server_task(self, task):
        result = []
        if type(task) == list:
            for t in task:
                result.append(await self.command_executor.run_command(t))
        else:
            result.append(await self.command_executor.run_command(task))
            
        return "\n".join(result);

    async def execute_cycle(self, dynamic_part):
        self.reset_daily_budget()
        cycle_dir = self.create_cycle_directory()

        task_data = await self.generate_task_with_gpt4(dynamic_part)
        if task_data != "API call limit reached for today.":
            task_data_json = json.loads(task_data)
            command_to_execute = task_data_json.get("cmd")
            human_task = task_data_json.get("ask")
            next_prompt = task_data_json.get("prompt")

            if human_task:
                self.write_to_file(cycle_dir, "ask.json", {"task": human_task})
                human_input = await self.wait_for_human_input(cycle_dir)
                dynamic_part = json.dumps({"result": human_input, "next_prompt": next_prompt})
            elif command_to_execute:
                if command_to_execute == "exit":
                    exit(0)
                
                self.write_to_file(cycle_dir, "cmd.json", {"command": command_to_execute})
                command_output = await self.execute_server_task(command_to_execute)
                self.write_to_file(cycle_dir, "results.json", {"result": command_output})
                dynamic_part = json.dumps({"result": command_output, "next_prompt": next_prompt})
            else:
                dynamic_part = json.dumps({"result": "", "next_prompt": next_prompt})

            return dynamic_part
        else:
            return dynamic_part

class CommandExecutor:
    def __init__(self, timeout=60):
        self.timeout = timeout

    async def run_command(self, cmd):
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return f"Command '{cmd}' timed out and was killed."

        if process.returncode == 0:
            return "Command executed successfully."
        else:
            return f"Command '{cmd}' failed with return code {process.returncode}."

async def main():
    openai_api_key = os.getenv('OPENAI_API_KEY')
    daily_budget = 10  # Example daily budget
    command_timeout = 30  # Timeout for commands in seconds
    base_cycle_dir = "./cycles/"  # Set the path to the base cycle directory

    ai_manager = AIManager(openai_api_key, daily_budget, command_timeout, base_cycle_dir)

    dynamic_part = "Your first task should be to install this script as a service and set it to start on reboot. Then review the script for cost efficiencies. You then have free choice on how to proceed to profit within aws rules."
    while True:  # or some other condition to continue running
        dynamic_part = await ai_manager.execute_cycle(dynamic_part)

# Run the main function asynchronously
asyncio.run(main())
