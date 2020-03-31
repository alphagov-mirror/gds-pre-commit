import glob
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime

import boto3
import fire
import git

def get_paged_ssm_params(path: str) -> list:
    """ Get SSM parameters into single array from 10 item pages """
    ssm_client = boto3.client("ssm")
    has_next_page = True
    ssm_params = []
    token = None
    while has_next_page:

        client_params = {
            "Path": path,
            "Recursive": True,
            "WithDecryption": True,
            "MaxResults": 10,
        }
        if token:
            client_params["NextToken"] = token

        response = ssm_client.get_parameters_by_path(**client_params)
        ssm_params = ssm_params + response["Parameters"]
        if "NextToken" in response:
            token = response["NextToken"]
            has_next_page = True
        else:
            token = None
            has_next_page = False
    return ssm_params


class DetectChecker:
    """ Test pre-commit secret detection """

    def __init__(self):
        """ Load template file contents """
        self.log = logging.getLogger("detect-check")
        self.log.setLevel(logging.DEBUG)
        self.templates = {}
        self.tests = []
        for template in glob.glob("templates/*"):
            file_name_components = template.split("/")
            file_name = file_name_components.pop()
            template_file = open(template, "r")
            self.templates[file_name] = template_file.read()
            template_file.close()

    def generate_temp_file_name(
        self, source: str, secret_type: str, template: str
    ) -> str:
        template_components = template.split(".")
        extension = template_components.pop()
        file_name = ".".join(template_components)

        temp_file_words = re.split("[-_. ]", f"commit_{source}_{secret_type}")
        if extension == "java":
            temp_file_name = " ".join(temp_file_words).title().replace(" ", "")
            temp_file_name += file_name
        else:
            temp_file_name = "_".join(temp_file_words).lower()
            temp_file_name += f"_{file_name}"

        commit_file = f"commits/{temp_file_name}.{extension}"
        return commit_file

    def _populate_templates(self, source: str, secret_type: str, value: str) -> None:
        """ Make a temporary python file """
        # Generate temp filename from secret type
        for template, content in self.templates.items():
            commit_file = self.generate_temp_file_name(source, secret_type, template)
            extension = commit_file.split(".").pop()

            with open(commit_file, "w") as code_file:
                multi_line = value.replace('"', '\\"')
                single_line = (
                    value.encode("unicode_escape").decode().replace('"', '\\"')
                )
                code_content = content.replace("%SINGLE_LINE_VARIABLE%", single_line)
                code_content = code_content.replace("%MULTI_LINE_VARIABLE%", multi_line)
                code_file.write(code_content)
                self.tests.append(
                    {
                        "source": source,
                        "secret_type": secret_type,
                        "test_file": commit_file,
                        "file_type": extension,
                    }
                )

    @classmethod
    def cleanup(cls):
        """ Clean up previous run """
        for example in glob.glob("commits/*"):
            if "README.md" not in example:
                os.remove(example)

    def _build_commitable_temp_files(self):
        """ Iterate over secret types and populate into temp files from
self.templates """
        ssm_params = get_paged_ssm_params("/detect-secrets/example-data")

        count = 0
        for param in ssm_params:
            count += 1
            parents = param["Name"].split("/")
            secret_type = parents.pop()
            source = parents.pop()
            print(f"Created test commit for {count}: {source} - {secret_type}")

            self._populate_templates(source, secret_type, param["Value"])

    def _load_repo(self):
        self.repo = git.repo.base.Repo("..")
        self.parent_branch = self.repo.head.object.hexsha
        print(f"Currently on branch: {self.parent_branch}")
        return self.repo

    def _checkout_test_branch(self):
        """ Checkout a new branch to run commit tests against """
        now = datetime.utcnow()
        timestamp = now.strftime("%Y-%m-%d_%H%M")
        self.branch_name = f"detect_test_{timestamp}"
        self.test_branch = self.repo.create_head(self.branch_name)
        self.test_branch.checkout()
        self._remove_ignore_file()
        return self.test_branch

    def _delete_test_branch(self):
        """ Delete local test branch """
        self._restore_ignore_file()
        self.repo.head.reference = self.parent_branch
        self.repo.delete_head(self.branch_name, force=True)

    def _test_commit(self, example_file: str) -> bool:
        """ Try committing and reset on success """
        relative_path = f"test-detection/{example_file}"
        index = self.repo.index
        detected = False
        try:
            index.add([relative_path])  # add a new file to the index
            commit_message = f"Test committing {example_file}"
            index.commit(commit_message)
            self.repo.active_branch.commit = self.repo.commit("HEAD~1")
            index.remove([relative_path])
            self.log.error(f"Fail: Secret not detected for {example_file}")
        except git.exc.HookExecutionError as err:
            self.log.error(f"Pass: Secret detected for {example_file}: ")
            self.log.debug(str(err))
            index.remove([relative_path])
            detected = True
        except (git.GitCommandError, FileNotFoundError) as err:
            self.log.error(f"Fail: Other error {example_file}: " + str(err))
            index.remove([relative_path])
        return detected

    def _remove_ignore_file(self):
        """ Remove .gitignore file so the commit files are not ignored by
detect-secrets """
        self.repo.index.remove([".gitignore"], working_tree=True)
        commit_message = f"Remove ignore file for testing"
        self.repo.index.commit(commit_message)

    def _restore_ignore_file(self):
        """ Restore .gitignore file """
        self.repo.git.reset("--hard", "HEAD^1")

    def check(self):
        """ Run checks if AWS credentials present """
        self.cleanup()
        if "AWS_ACCESS_KEY_ID" in os.environ:
            self.branch()

            self._build_commitable_temp_files()
            status = defaultdict(list)
            for test in self.tests:
                example = test["test_file"]
                if "README.md" not in example:
                    detected = self._test_commit(example)
                    outcome = "passed" if detected else "failed"
                    status[outcome].append(example)
                    test["detected"] = detected
                    test["outcome"] = outcome

            self._delete_test_branch()
            print(f"Reset to parent branch: {self.repo.head.object.hexsha}")

            language_stats = defaultdict(dict)
            secret_stats = defaultdict(dict)
            for test in self.tests:

                lang = test["file_type"]
                if lang not in language_stats:
                    language_stats[lang] = defaultdict(int)

                secret_type = f"{test['source']}: {test['secret_type']}"
                if secret_type not in secret_stats:
                    secret_stats[secret_type] = defaultdict(int)

                language_stats[lang]["total"] += 1
                secret_stats[secret_type]["total"] += 1
                if test["detected"]:
                    language_stats[lang]["passed"] += 1
                    secret_stats[secret_type]["passed"] += 1
                else:
                    language_stats[lang]["failed"] += 1
                    secret_stats[secret_type]["failed"] += 1

            # print(json.dumps(language_stats, indent=4))
            # print(json.dumps(secret_stats, indent=4))
            print("\n\nStats by secret type\n")
            for secret_type in sorted(secret_stats.keys()):
                print(
                    f"{secret_type} "
                    + f"{secret_stats[secret_type]['passed']} "
                    + f"{secret_stats[secret_type]['failed']} "
                    + f"{secret_stats[secret_type]['total']}"
                )

            print("\n\nStats by template type\n")
            for lang in sorted(language_stats.keys()):
                print(
                    f"{lang} "
                    + f"{language_stats[lang]['passed']} "
                    + f"{language_stats[lang]['failed']} "
                    + f"{language_stats[lang]['total']}"
                )

            # print(json.dumps(status, indent=4))
            print("\n\nOverall success rate\n")
            stats = {"passed": 0, "failed": 0}
            stats.update({category: len(files) for category, files in status.items()})

            stats["total"] = stats["passed"] + stats["failed"]
            success_rate = 100 * stats["passed"] / stats["total"]

            stats["success_rate"] = 100 * stats["passed"] / stats["total"]

            print(json.dumps(stats, indent=4))

            print(f"OKR detection rate: {success_rate:.1f}%")

        else:
            print("No AWS credentials present. Run with AWS credentials.")

    def branch(self):
        self.cleanup()
        self._load_repo()
        self._checkout_test_branch()
        print(f"Testing on branch: {self.repo.active_branch.name}")

    def build_tests(self):
        if "AWS_ACCESS_KEY_ID" in os.environ:
            self.cleanup()
            self._build_commitable_temp_files()
        else:
            print("No AWS credentials present. Run with AWS credentials.")


if __name__ == "__main__":
    """ Implement DetectChecker class with Fire CLI wrapper """
    fire.Fire(DetectChecker())
