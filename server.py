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


def format_message(payload, target):
    """Format json message"""
    return json.dumps({"target": target, "payload": payload})


def announce(message, name=None, target="message"):
    """Make broadcast"""
    broadcast(
        db.conn,
        format_message(
            target=target, payload=f"{name}: {message}" if name else message
        ),
    )


def server_announce(message):
    announce(message, name="SERVER")


async def proceed_game():
    """Process game to the next question"""
    try:
        server_announce(
            rf"Question {game.cur_q} will start in {game.interval_time} seconds. \\(^_^)//"
        )
        await asyncio.sleep(game.interval_time - game.count_down_time)

        for i in range(game.count_down_time, 0, -1):
            server_announce(
                f"Question {game.cur_q} will start in {i} seconds."
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


def update_leaderboard(boards):
    """Update leaderboard on the server and braodcast to everyone connected"""
    print("Updating leader board")
    for user, score in boards:
        db.users[user]["score"] += score

    sorted_score = sorted(db.users.items(), key=lambda x: x[-1]["score"], reverse=True)
    db.leaderboad = [(user, info["score"]) for user, info in sorted_score]
    announce(db.leaderboad, target="leaderboard")
    server_announce("The leaderboard has been updated!")
    print("Leaderboard has been updated", db.leaderboad)


async def check_num_user():
    if len(db.conn) == 0:
        print("No user left in the room. Stopping any ongoing games.")
        await stop_game()


async def register(ws, name: str):
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

    db.conn[ws] = name

    if not db.users.get(name):
        db.users[name] = {"score": 0}

    db.users[name]["active"] = True

    try:
        await ws.send(format_message(target="register", payload="success"))
        await ws.send(format_message(target="leaderboard", payload=db.leaderboad))
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
            update_leaderboard(score_board)

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
            asyncio.create_task(register(websocket, request["name"]))

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
