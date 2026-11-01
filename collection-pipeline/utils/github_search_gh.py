from github import Github
import os

# Authenticate with a token
token = os.getenv("GITHUB_TOKEN")
gh = Github(token)

# Build your search query
query = "machine learning in:readme in:description"
results = gh.search_repositories(query)

for repo in results:
    print(repo.full_name, repo.html_url)
