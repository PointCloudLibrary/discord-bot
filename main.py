#! /usr/bin/env python3

"""
Software License Agreement (BSD License)

 Point Cloud Library (PCL) - www.pointclouds.org
 Copyright (c) 2020-, Open Perception, Inc.

 All rights reserved.

 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:

  * Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
  * Redistributions in binary form must reproduce the above
    copyright notice, this list of conditions and the following
    disclaimer in the documentation and/or other materials provided
    with the distribution.
  * Neither the name of the copyright holder(s) nor the names of its
    contributors may be used to endorse or promote products derived
    from this software without specific prior written permission.

 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
 ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 POSSIBILITY OF SUCH DAMAGE.

"""

import aiohttp
import argparse
import asyncio
from datetime import datetime
import discord
from discord.ext import commands
import itertools
import json
import os.path as path
import random
import time
from urllib.parse import quote_plus


def read_config(filename):
    with open(filename, "r") as f:
        return json.load(f)


bot = commands.Bot(command_prefix="!")
command_config = read_config("command_config.json")
config = {}
gh_auth = None


async def github_ratelimiter(headers, error_channel):
    # If this is the last message before rate-limiting kicks in
    if int(headers["X-RateLimit-Remaining"]) == 1:
        epoch_sec = int(headers["X-RateLimit-Reset"])
        delay = datetime.fromtimestamp(epoch_sec) - datetime.now()
        # adding 1 to ensure we wait till after the rate-limit reset
        sleep_time = delay.total_seconds() + 1
        if sleep_time > 61:  # Waiting more than a minute is not kinda-sorta ok
            await error_channel(
                "API Request timed out", f"Try again after {sleep_time} seconds"
            )
            return sleep_time
        print(f"Need to sleep for {sleep_time}")
        await asyncio.sleep(sleep_time)
    return 0


def error_handler(channel):
    error = discord.Embed(color=discord.Color.red())

    async def handler(title, description):
        error.title = title
        error.description = description
        await channel.send(embed=error)

    return handler


async def default_error_handler(title, description):
    return True


async def get_issues(
    repository="PointCloudLibrary/pcl",
    closed=False,
    pull_request=False,
    include_labels=[],
    exclude_labels=[],
    sort="created",
    ascending_order=False,
    error_channel=default_error_handler,
):
    closed = "closed" if closed else "open"
    pull_request = "pr" if pull_request else "issue"
    print(f"Getting list of {closed} {pull_request} from GitHub")

    def gh_encode(x):
        return quote_plus(x, safe='"')

    api_url = "https://api.github.com/search/issues?"
    # All query items are list of strings, which will be flattened later
    excluded_labels = [f'-label:"{gh_encode(x)}"' for x in exclude_labels]
    included_labels = [f'label:"{gh_encode(x)}"' for x in include_labels]
    issue = [f"is:{pull_request}"]
    repo = [f"repo:{repository}"]
    status = [f"is:{closed}"]
    query = [issue, repo, status, excluded_labels, included_labels]

    query_string = "q=" + "+".join(itertools.chain.from_iterable(query))
    sort_string = f"sort={sort}"
    order_string = "order=" + ("asc" if ascending_order else "desc")

    query_url = api_url + "&".join([query_string, sort_string, order_string])

    print(query_url)

    data_count = 0
    page = 1
    while True:
        # max pagination size is 100 as of github api v3
        search_url = f"{query_url}&page={page}&per_page=100"
        async with aiohttp.ClientSession() as session:
            try:
                response = await session.get(
                    search_url, raise_for_status=True, headers=gh_auth
                )
            except TimeoutError:
                await error_channel(
                    "API Request timed out",
                    "Please check https://www.githubstatus.com/",
                )
                break
            async with response:
                data = await response.json()
                total_count = data["total_count"]
                data_count += len(data["items"])
                for item in data["items"]:
                    yield item
                page += 1
                if await github_ratelimiter(response.headers, error_channel):
                    break
                # exit if all data has been received
                if data_count == total_count:
                    break
    print(f"Found {data_count} entries")


async def get_pr_details(issues, error_channel=lambda title, desc: True):
    print("Getting more details about the PRs")
    counter = 0
    for issue in issues:
        async with aiohttp.ClientSession() as session:
            try:
                response = await session.get(
                    issue["pull_request"]["url"], raise_for_status=True, headers=gh_auth
                )
            except TimeoutError:
                await error_channel(
                    "API Request timed out",
                    "Please check https://www.githubstatus.com/",
                )
                break
            async with response:
                pr_data = await response.json()
                counter += 1
                yield pr_data
                if await github_ratelimiter(response.headers, error_channel):
                    break
    print(f"Received data about {counter} PRs")


async def pr_with_pending_review(pr_list, user):
    """
    Generates PR which need to be reviewed by the user
    """
    print(f"Filtering for @{user}")
    async for pr in pr_list:
        for reviewer in pr["requested_reviewers"]:
            if reviewer["login"] == user:
                yield pr


def beautify_issues(github_issue_list):
    req_details = ["title", "body", "html_url", "created_at", "updated_at"]
    return [{x: issue[x] for x in req_details} for issue in github_issue_list]


def compose_message(issues):
    issue_data = [
        f'**{i+1}.** {issue["title"]}\n  {issue["html_url"]}'
        for i, issue in enumerate(issues)
    ]
    return "\n".join(issue_data)


async def set_playing(status):
    await bot.change_presence(activity=discord.Game(name=status))


@bot.event
async def on_ready():
    await set_playing("The Waiting Game")


async def check_number_of_issues(number_of_issues, error_channel=default_error_handler):
    if number_of_issues < 1:
        number_of_issues = 10
        await error_channel(
            "Woah, there!", "I can't give you un-natural issues. I'm not a monster!!",
        )
    if number_of_issues > 10:
        number_of_issues = 10
        await error_channel(
            "Woah, there!", "Let's curb that enthusiasm.. just a little"
        )
    return number_of_issues


async def check_pull_request(noun, error_channel=default_error_handler):
    pull_request = False
    if noun in ["issue", "issues", "ISSUE", "ISSUES"]:
        pull_request = False
    elif noun in ["pr", "prs", "PR", "PRs", "PRS"]:
        pull_request = True
    else:
        await error_channel("", "Insufficient info, defaulting to issues..")
    return pull_request


async def check_author(ctx, noun, error_channel=default_error_handler):
    author = None
    if noun in ["issue", "issues", "ISSUE", "ISSUES"]:
        await error_channel(
            "Woah, there!", "Sorry, but we don't review issues here. Up for some PRs?"
        )
    if noun is None or noun not in ["all", "ALL"]:
        author = ctx.message.author
        if isinstance(author, discord.Member):
            author = author.nick or author.name
        elif isinstance(author, discord.User):
            author = author.name
    return author


async def choose_rand(issues, number_of_issues):
    """
    @TODO: improve algorithm to save queries
    1. get first batch of github_max (100), find total number
    2. if total < requested, return all
    3. Generate requested random numbers
    4. get issues till max(generated_list)
    5. return them
    """
    chosen_issues = random.choices(issues, k=number_of_issues)
    title = f"{number_of_issues} random picks out of {len(issues)}:"

    return chosen_issues, title


async def choose_review(issues, number_of_issues, author):
    chosen_issues = []
    if author:
        issues = pr_with_pending_review(get_pr_details(issues), author)
        # since async islice doesn't exist
        async for issue in issues:
            chosen_issues.append(issue)
            if len(chosen_issues) == number_of_issues:
                break
    else:
        chosen_issues = issues[:number_of_issues]

    selection = f"for @{author}" if author else f"in review queue"
    title = f"Oldest {number_of_issues} PR(s) {selection}:"

    return chosen_issues, title


async def choose_feedback(issues, number_of_issues, pull_request):
    chosen_issues = issues[:number_of_issues]

    kind = "PR(s)" if pull_request else "issue(s)"
    title = f"Oldest {number_of_issues} {kind} in feedback queue:"

    return chosen_issues, title


for name, conf in command_config.items():
    # name=name and conf=conf used to prevent late binding
    @bot.command(name=name)
    async def command_function(
        ctx, number_of_issues: int, noun=None, channel=None, name=name, conf=conf
    ):
        reply = discord.Embed(color=discord.Color.purple())
        if channel is None:
            channel = ctx.channel
        error_channel = error_handler(channel)

        def delay(x, *args, **kwargs):
            async def actual_waiter():
                return await x(*args, **kwargs)

            return actual_waiter

        check_order = [
            delay(
                check_number_of_issues,
                number_of_issues=number_of_issues,
                error_channel=error_channel,
            ),
            delay(check_pull_request, noun=noun, error_channel=error_channel),
            delay(check_author, ctx=ctx, noun=noun, error_channel=error_channel),
        ]

        # command specific checks
        if name in ["rand", "fq"]:
            number_of_issues = await check_order[0]()
            pull_request = await check_order[1]()
            author = None
        if name in ["rq"]:
            number_of_issues = await check_order[0]()
            pull_request = True
            author = await check_order[2]()

        await set_playing("On The Cue")
        async with channel.typing():
            issues = [
                x
                async for x in get_issues(
                    **conf, pull_request=pull_request, error_channel=error_channel
                )
            ]

            choose_for_commands = {
                "rand": delay(
                    choose_rand, issues=issues, number_of_issues=number_of_issues
                ),
                "rq": delay(
                    choose_review,
                    issues=issues,
                    number_of_issues=number_of_issues,
                    author=author,
                ),
                "fq": delay(
                    choose_feedback,
                    issues=issues,
                    number_of_issues=number_of_issues,
                    pull_request=pull_request,
                ),
            }
            chosen_issues, reply.title = await choose_for_commands[name]()

            reply.description = compose_message(beautify_issues(chosen_issues))
            if len(chosen_issues) < number_of_issues:
                reply.set_footer(text="There weren't enough...")
        await channel.send(embed=reply)
        await set_playing("The Waiting Game")


@bot.command(name="what")
async def what_cmd(ctx):
    reply = discord.Embed(color=discord.Color.purple())
    reply.title = "Command list for GitHub Helper"
    reply.description = """`!rand <N> issue/pr`
Retrieves N random open, non-stale issue(s)/PR(s)

`!rq <N> [all]`
Retrieves least-recently-updated PR(s) and filters those awaiting a review from you (default) or anyone (in presence of all)

`!fq <N> issue/pr`
Retrieves N least-recently-updated issue(s)/PR(s) in the feedback queue"""
    await ctx.channel.send(embed=reply)


@bot.event
async def on_command_error(ctx, error):
    reply = discord.Embed(color=discord.Color.purple())
    reply.description = "Talking to me? Use `!what` to know more."
    if (
        isinstance(error, discord.ext.commands.errors.BadArgument)
        or isinstance(error, discord.ext.commands.errors.MissingRequiredArgument)
        or isinstance(error, discord.ext.commands.errors.CommandNotFound)
        or isinstance(error, discord.ext.commands.errors.DiscordException)
    ):
        await ctx.channel.send(embed=reply)


# deprecated commands
for name in ["review", "q"]:

    @bot.command(name=name)
    async def deprecated_cmd(ctx):
        reply = discord.Embed()
        reply.title = "Deprecated command"
        reply.description = "Use `!what` to know more."
        reply.set_image(
            url="https://media.giphy.com/media/kegHkRsheJk3fjOg3D/giphy.gif"
        )
        await ctx.channel.send(embed=reply)


async def oneshot(channel_id, n):
    await bot.wait_until_ready()
    await command_function(
        ctx=None,
        number_of_issues=n,
        noun="issue",
        channel=bot.get_channel(channel_id),
        name="rand",
        conf=command_config["rand"],
    )
    await bot.close()


def readable_file(string):
    if path.isfile(string):
        return string
    raise argparse.ArgumentTypeError(f"'{string}' is not a valid readable file")


def get_args():
    p = argparse.ArgumentParser(
        "GitHub Issue Slot Machine",
        description="""[Discord bot]
It helps to discover random open, non-stale issues.
By default, it'll enter interactive mode and return N issues when prompted by:
`!give N`
where N is a number less than open issues.
If a channel ID is provided, it'll send N issues and exit
""",
    )
    p.add_argument(
        "--channel_id", type=int, help="Channel ID (numerical) to send messages to"
    )
    p.add_argument(
        "--issues",
        metavar="N",
        default=5,
        type=int,
        help="Number of issues to send in one-shot mode, default: 5",
    )
    p.add_argument(
        "--config",
        type=readable_file,
        default="config.json",
        help="location of config file",
    )
    return p.parse_known_args()


def main():
    args, _ = get_args()

    global config
    config = read_config(args.config)
    print(f"Setting bot for {config['repo']}")

    gh_token = config.get("github_token", None)
    global gh_auth
    gh_auth = {"Authorization": f"token {gh_token}"} if gh_token else None

    if not (args.channel_id and args.issues > 0):
        print("Entering interactive mode")
        bot.run(config["discord_token"])
        return

    print(
        "Running in one-shot mode."
        f" Will send {args.issues} messages to requested channel"
    )
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot.login(config["discord_token"]))
    loop.create_task(oneshot(args.channel_id, args.issues))
    loop.run_until_complete(bot.connect())


if __name__ == "__main__":
    main()
