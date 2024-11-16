#!/usr/bin/env python

import asyncio
import json
import random
import re
from dataclasses import dataclass, field

from websockets import ConnectionClosedError
from websockets.asyncio.server import broadcast, serve


@dataclass
class DB:
    users: dict = field(default_factory=dict)
    leaderboad: list = field(default_factory=list)
    conn: dict = field(default_factory=dict)


@dataclass
class Game:
    active: bool = False
    check_answer: bool = False
    stopping: bool = False
    tot_q: int = 0
    cur_q: int = 0
    solved: bool = False
    question: str = ""
    answer: int = 0
    score: int = 0
    difficulty: str = "easy"
    bound: tuple[int, int] = (0, 0)
    count_down_time: int = 3
    interval_time: int = 10
    user_score: dict = field(default_factory=dict)
    cur_game: asyncio.Task | None = None


game = Game()

db = DB()


def start_game(command):
    _, difficulty, num_q = command.split()
    game.tot_q = int(num_q)
    if game.tot_q < 1:
        raise ValueError("Number of games must be >= 1")
    game.bound = {"easy": (0, 100), "medium": (-100, 100), "hard": (-1000, 1000)}[
        difficulty
    ]
    game.score = {"easy": 10, "medium": 20, "hard": 30}[difficulty]
    game.difficulty = difficulty
    game.cur_q = 0
    game.user_score = {user: 0 for user in db.users if db.users[user]["active"]}
    game.active = True


def format_message(payload, target, color=None):
    """Format json message"""
    return json.dumps({"target": target, "payload": payload, "color": color})


def announce(message, name=None, target="message"):
    """Make broadcast"""
    broadcast(
        db.conn,
        format_message(
            target=target,
            payload=f"{name}: {message}" if name else message,
            color=name,
        ),
    )


def server_announce(message):
    announce(message, name="SERVER")


async def proceed_game():
    """Process game to the next question"""
    try:
        delay = game.interval_time - game.count_down_time
        if delay > 0:
            server_announce(
                rf"Question {game.cur_q} will start in {game.interval_time} seconds. \\(^_^)//"
            )
            await asyncio.sleep(game.interval_time - game.count_down_time)

        for i in range(game.count_down_time, 0, -1):
            server_announce(
                f"Question {game.cur_q} will start in {i} seconds. "
                + (r"\\(^_^)\\" if i % 2 == 0 else r"//(^_^)//")
            )
            await asyncio.sleep(1)

        x = random.randint(*game.bound)
        y = random.randint(*game.bound)
        sign = random.choice(["+", "-"])
        game.question = f"{x} {sign} {y}"
        game.answer = eval(game.question)
        game.solved = False

        game.check_answer = True
        server_announce(f"Question {game.cur_q}: {game.question} = ❓")
    except asyncio.CancelledError:
        print("Task proceed_game was cancelled!")


async def check_to_proceed():
    """Check if we should continue a game"""
    if game.cur_q < game.tot_q:
        game.cur_q += 1
        game.cur_game = asyncio.create_task(proceed_game())
    elif not game.stopping:
        await stop_game()


async def check_answer(answer: str, name: str):
    """Check game answer"""
    if game.solved:
        return

    try:
        answer = int(answer)
    except ValueError:
        print(f"Ignore answer {answer} as it's not convertable.")
        return

    if answer == game.answer:
        game.solved = True
        server_announce(
            f"Answer {answer} is correct ✔✔ {name} got {game.score} points."
        )
        game.user_score[name] += game.score

        game.check_answer = False

        await check_to_proceed()
    else:
        server_announce(f"Answer {answer} is incorrect - {name}. Try again 🙊")


def update_leaderboard(scores: dict):
    """Update leaderboard on the server and braodcast to everyone connected"""
    print("Updating leader board")
    new_boards = []

    # Current user in score board
    for user, score in db.leaderboad:
        if user in scores:
            score += scores[user]
            del scores[user]
        new_boards.append((user, score))

    # New users to be added to score board
    for user, score in scores.items():
        new_boards.append((user, score))

    db.leaderboad = sorted(new_boards, key=lambda x: x[-1], reverse=True)
    announce(db.leaderboad, target="leaderboard")
    server_announce("The leaderboard has been updated!")
    print("Leaderboard has been updated", db.leaderboad)


def reset_leaderboard():
    """Reset leader board and user score"""
    db.leaderboad = []
    announce(db.leaderboad, target="leaderboard")
    server_announce("The leaderboard has been reset!")
    print("Leaderboard has been reset!")


def update_palette(palette):
    """Update palette of all users"""
    print("updating palette")
    announce(palette, target="palette")


async def check_num_user():
    if len(db.conn) == 0:
        print("No user left in the room. Stopping any ongoing games.")
        await stop_game()


def get_all_palette():
    """Initialize palette for a new user"""
    palette = [("SERVER", "dark green", ""), ("help", "yellow", "")]

    for user, info in db.users.items():
        if info["active"]:
            palette.append((user, info["foreground"], info["background"]))

    print("all palette is", palette)
    return palette


def parse_color(color: str):
    """parse color string to foreground and background"""
    if color is None or color == "":
        return "", ""

    colors = color.split(",")

    if len(colors) == 1:
        return colors[0].strip(), ""

    return colors[0].strip(), colors[1].strip()


async def register(ws, name: str, color: str):
    """Register new user"""
    if game.active:
        await ws.send(
            format_message(
                payload="Can't join during an ongoing game. Please wait", target="error"
            )
        )
        return

    if name in db.users and db.users[name]["active"]:
        await ws.send(
            format_message(target="error", payload=f"Name {name} is already in use")
        )
        return

    db.users[name] = {"active": True}

    foreground, background = parse_color(color)
    db.users[name]["foreground"] = foreground
    db.users[name]["background"] = background

    try:
        await ws.send(format_message(target="register", payload="success"))
        await ws.send(format_message(target="palette", payload=get_all_palette()))
        await ws.send(format_message(target="leaderboard", payload=db.leaderboad))

        update_palette([(name, foreground, background)])

        db.conn[ws] = name

        server_announce(f"User {name} has join the room")
        print(f"User {name} has joined the room")
        await ws.wait_closed()
    finally:
        del db.conn[ws]
        db.users[name]["active"] = False
        print(f"User {name} has left")
        server_announce(f"User {name} has left the room")
        await check_num_user()


async def stop_game():
    """Stop a running game"""
    game.check_answer = False
    game.active = False
    game.stopping = True

    if game.cur_game is not None and not game.cur_game.done():
        game.cur_game.cancel()

    server_announce("Game has ended !!!")

    if len(game.user_score) == 1:
        server_announce(f"Your total score is {list(game.user_score.values())[0]} 👍")
    elif len(game.user_score) > 1:
        score_board = sorted(game.user_score.items(), key=lambda x: x[-1], reverse=True)
        scores = []
        for idx, user_score in enumerate(score_board, 1):
            scores.append(f"     {idx}. {user_score[0]} - {user_score[1]} points")
            formatted_score = "\n".join(scores)
        server_announce(f"Here is the scoreboard of this round!\n{formatted_score}")

        await asyncio.sleep(0.5)

        if score_board[0][-1] > score_board[1][-1]:
            server_announce(f"{score_board[0][0]} is the winner!")
        else:
            server_announce("The scores are tied!")

        if sum(score for _, score in score_board) > 0:
            update_leaderboard(game.user_score)

    game.stopping = False


async def resolve_command(command: str, name: str):
    if command == "/stop":
        server_announce(f"User {name} has stopped the game.")
        await stop_game()

    elif game.active:
        server_announce(
            f"Failed to proceed with command {command}. Due to an ongoing game."
        )
    elif command.startswith("/play"):
        try:
            start_game(command)
            server_announce(
                f"New Game has been started by {name}.\n"
                f"      Question difficulty: {game.difficulty}\n"
                f"      Number of questions: {game.tot_q}\n"
                f"      Number of players:   {len(game.user_score)} ({' 🆚 '.join(game.user_score.keys())})"
            )
            game.cur_game = asyncio.create_task(check_to_proceed())
        except:
            server_announce(
                "Failed to start game. Hint: use `/play <difficulty> <num-questions>`."
            )
    elif command == "/reset":
        reset_leaderboard()

    elif command.startswith("/set-delay"):
        try:
            _, _time = command.split()
            time = int(_time)
            bound = (3, 20)
            if time < bound[0]:
                server_announce(f"Delay time can't be lower than {bound[0]}")
            elif time > bound[1]:
                server_announce(f"Delay time can't be lower than {bound[1]}")
            else:
                game.interval_time = time
                server_announce(
                    f"The delay time between questions has been set to {time} second(s)"
                )
        except:
            server_announce("Failed to set delay time. Hint: Use `/set-delay <second>`")


async def hello(websocket):
    while True:
        try:
            message = await websocket.recv()
        except ConnectionClosedError:
            print("Connection closed by client.")
            break

        try:
            request = json.loads(message)
        except:
            print("Fail to parse json")
            continue

        if request["action"] == "register":
            asyncio.create_task(register(websocket, request["name"], request["color"]))

        if websocket in db.conn:
            if request["action"] == "message":

                payload = request["payload"]

                name = db.conn[websocket]

                announce(payload, name=name)

                if game.active:
                    await check_answer(payload, name)

                if payload.startswith("/"):
                    await resolve_command(payload, name)


async def main():
    print("Starting server ...")
    async with serve(hello, "", 8765):
        await asyncio.get_running_loop().create_future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
