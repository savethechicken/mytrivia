from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


class GamePhase(str, Enum):
    IDLE = "IDLE"
    ROUND_VOTE = "ROUND_VOTE"
    ROUND_INTRO = "ROUND_INTRO"
    QUESTION_ACTIVE = "QUESTION_ACTIVE"
    QUESTION_REVEAL = "QUESTION_REVEAL"
    ROUND_END = "ROUND_END"
    GAME_OVER = "GAME_OVER"


class ChatMessageIn(BaseModel):
    platform: str = "twitch"
    user_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    color: str | None = None
    text: str


@dataclass(slots=True)
class PlayerState:
    key: str
    display_name: str
    color: str
    score: int = 0
    first_score_seq: int = 10**9


@dataclass(slots=True)
class QuestionState:
    id: str
    category: str
    text: str
    answers: list[str]
    correct_index: int


@dataclass(slots=True)
class TriviaGame:
    lock: RLock = field(default_factory=RLock)
    phase: GamePhase = GamePhase.IDLE
    round_number: int = 0
    question_number: int = 0
    max_rounds: int = 3
    questions_per_round: int = 10
    timer_seconds: int = 30
    score_seq: int = 0
    players: dict[str, PlayerState] = field(default_factory=dict)
    current_question: QuestionState | None = None
    accepted_answers: dict[str, int] = field(default_factory=dict)
    category_votes: dict[str, int] = field(default_factory=dict)
    round_categories: list[str] = field(
        default_factory=lambda: ["Science", "Games", "History", "Entertainment"]
    )

    def _new_static_question(self) -> QuestionState:
        return QuestionState(
            id=f"static_r{self.round_number}_q{self.question_number}",
            category="General Knowledge",
            text="Which number should chat send for answer slot #2?",
            answers=["1", "2", "3", "4"],
            correct_index=1,
        )

    def _ensure_player(self, platform: str, user_id: str, display_name: str, color: str | None) -> PlayerState:
        key = f"{platform}:{user_id}"
        if key not in self.players:
            self.players[key] = PlayerState(
                key=key,
                display_name=display_name,
                color=color or "#FFFFFF",
            )
        else:
            p = self.players[key]
            p.display_name = display_name
            p.color = color or p.color or "#FFFFFF"
        return self.players[key]

    def start(self) -> dict[str, str]:
        with self.lock:
            self.phase = GamePhase.ROUND_VOTE
            self.round_number = 1
            self.question_number = 0
            self.current_question = None
            self.accepted_answers.clear()
            self.category_votes.clear()
            return {"status": "started", "phase": self.phase}

    def stop(self) -> dict[str, str]:
        with self.lock:
            self.phase = GamePhase.GAME_OVER
            return {"status": "stopped", "phase": self.phase}

    def reset(self) -> dict[str, str]:
        with self.lock:
            self.phase = GamePhase.IDLE
            self.round_number = 0
            self.question_number = 0
            self.score_seq = 0
            self.players.clear()
            self.current_question = None
            self.accepted_answers.clear()
            self.category_votes.clear()
            return {"status": "reset", "phase": self.phase}

    def skip(self) -> dict[str, str]:
        with self.lock:
            if self.phase == GamePhase.ROUND_VOTE:
                self.phase = GamePhase.ROUND_INTRO
            elif self.phase == GamePhase.ROUND_INTRO:
                self.phase = GamePhase.QUESTION_ACTIVE
                self.question_number += 1
                self.current_question = self._new_static_question()
                self.accepted_answers.clear()
            elif self.phase == GamePhase.QUESTION_ACTIVE:
                self.phase = GamePhase.QUESTION_REVEAL
            elif self.phase == GamePhase.QUESTION_REVEAL:
                if self.question_number >= self.questions_per_round:
                    if self.round_number >= self.max_rounds:
                        self.phase = GamePhase.GAME_OVER
                    else:
                        self.phase = GamePhase.ROUND_END
                else:
                    self.phase = GamePhase.QUESTION_ACTIVE
                    self.question_number += 1
                    self.current_question = self._new_static_question()
                    self.accepted_answers.clear()
            elif self.phase == GamePhase.ROUND_END:
                self.round_number += 1
                self.question_number = 0
                self.phase = GamePhase.ROUND_VOTE
                self.category_votes.clear()
            return {"status": "skipped", "phase": self.phase}

    def chat_message(self, msg: ChatMessageIn) -> dict[str, Any]:
        with self.lock:
            txt = msg.text.strip()
            if txt not in {"1", "2", "3", "4"}:
                return {"accepted": False, "reason": "invalid_input"}

            player = self._ensure_player(msg.platform, msg.user_id, msg.display_name, msg.color)

            if self.phase == GamePhase.ROUND_VOTE:
                if player.key in self.category_votes:
                    return {"accepted": False, "reason": "duplicate_vote"}
                self.category_votes[player.key] = int(txt) - 1
                return {"accepted": True, "kind": "vote", "choice": int(txt)}

            if self.phase == GamePhase.QUESTION_ACTIVE:
                if player.key in self.accepted_answers:
                    return {"accepted": False, "reason": "duplicate_answer"}

                answer = int(txt) - 1
                self.accepted_answers[player.key] = answer
                correct = self.current_question is not None and answer == self.current_question.correct_index
                if correct:
                    player.score += 100
                    if player.first_score_seq == 10**9:
                        player.first_score_seq = self.score_seq
                    self.score_seq += 1
                return {"accepted": True, "kind": "answer", "choice": int(txt), "correct": bool(correct)}

            return {"accepted": False, "reason": "phase_ignores_input"}

    def leaderboard(self) -> list[dict[str, Any]]:
        ranked = sorted(
            self.players.values(),
            key=lambda p: (-p.score, p.first_score_seq, p.display_name.lower()),
        )
        output: list[dict[str, Any]] = []
        for index, p in enumerate(ranked, start=1):
            output.append(
                {
                    "rank": index,
                    "key": p.key,
                    "display_name": p.display_name,
                    "color": p.color,
                    "score": p.score,
                }
            )
        return output

    def state(self) -> dict[str, Any]:
        with self.lock:
            winner_keys: list[str] = []
            board = self.leaderboard()
            if self.phase == GamePhase.GAME_OVER and board:
                top = board[0]["score"]
                winner_keys = [p["key"] for p in board if p["score"] == top]

            return {
                "phase": self.phase,
                "round_number": self.round_number,
                "question_number": self.question_number,
                "max_rounds": self.max_rounds,
                "questions_per_round": self.questions_per_round,
                "timer_seconds": self.timer_seconds,
                "categories": self.round_categories,
                "category_votes": self.category_votes,
                "question": (
                    {
                        "id": self.current_question.id,
                        "category": self.current_question.category,
                        "text": self.current_question.text,
                        "answers": self.current_question.answers,
                        "correct_index": self.current_question.correct_index,
                    }
                    if self.current_question
                    else None
                ),
                "answer_count": len(self.accepted_answers),
                "leaderboard": board,
                "winner_keys": winner_keys,
            }


game = TriviaGame()

app = FastAPI(title="Stream Trivia App", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/overlays", StaticFiles(directory="static/overlays"), name="overlays")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/game/start")
def game_start() -> dict[str, str]:
    return game.start()


@app.post("/game/stop")
def game_stop() -> dict[str, str]:
    return game.stop()


@app.post("/game/reset")
def game_reset() -> dict[str, str]:
    return game.reset()


@app.post("/game/skip")
def game_skip() -> dict[str, str]:
    return game.skip()


@app.post("/chat/message")
def chat_message(payload: ChatMessageIn) -> dict[str, Any]:
    return game.chat_message(payload)


@app.get("/game/state")
def game_state() -> dict[str, Any]:
    return game.state()


@app.get("/")
def root() -> FileResponse:
    return FileResponse("static/overlays/trivia.html")
