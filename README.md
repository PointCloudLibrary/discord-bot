# PCL GitHub Slot Machine

Simple discord bot

## Create a discord bot

1. Navigate to [Developers' Page](https://discordapp.com/developers/applications) for discord
2. Create a new application
3. Give it a name, image, description and all the works
4. In the '**Bot**' page of the Application, click on '**Add Bot**'
5. Give a name to you bot
6. Copy the token and put it in the config file

Make sure to never make your secret tokens public


## Invite the bot to your server

To generate the Bot invitation link:
1. Navigate to Oath2 page of your Application
2. Scroll down and select '**bot**' in the '**SCOPES**' section
3. Add the required permissions needed for your bot. By default, none are selected. This bot needs:
  * View Channels
  * Send Messages
4. Copy the updated link at the bottom of the '**SCOPES**' section
5. Make sure to give the link to a trusted parties only since it'll add the bot to whatever servers they choose to

## GitHub config

You can choose not to supply a GitHub token, in which case it'll use unauthorized API calls.
These are subject to a harsher rate-limit, but is usually sufficient for a low rate-of-use.

For the authorized access, create a **Personal Access Token** and add it to the config file.
