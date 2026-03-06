I want to build these automatic processes:

First process:
Trigger: every hour
1. receive a list of emails
2. For every new email, do the following:
2a. extract email content and attachement
2b. process them into a structued md file
3c. save the file into a folder called "requests"
3d. add the file to a log file

Second process:
Trigger - once a day
1. Look at the files under "requests" 
2. Find possible duplicates 
3. If there are duplicates: 
3a. create a new branch
3b. merge every duplications into a unified document and remove the old files 
3c. commit, push and create a pull request with 

Third process:
Trigger: when new files are added to the repository
1. add new files under requests/ to notebooklm as sources
2. remove files that don't exist in requests/
3. log changes to a log file 
4. Finally, update a source file on notebooklm documenting the last updated timestamp

Orchestrator: Github workflows

Trigger: Every hour, if there are new emails

Gmail CLI utility:
https://github.com/googleworkspace/cli

NotebookLM CLI utility:
https://github.com/teng-lin/notebooklm-py