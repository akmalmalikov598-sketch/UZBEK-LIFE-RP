import time
import math

class Player:
    def __init__(self, pid, char_id, username, ws):
        self.id = pid
        self.char_id = char_id
        self.username = username
        self.ws = ws
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.rot = 0.0
        self.last_update = time.time()
        self.last_move_time = time.time()

class GameState:
    def __init__(self):
        self.players = {}
        
    def add_player(self, player):
        self.players[player.id] = player
        
    def remove_player(self, pid):
        if pid in self.players:
            del self.players[pid]
            
    def get_state(self):
        return [
            {"id": p.id, "u": p.username, "x": round(p.x, 2), "y": round(p.y, 2), "z": round(p.z, 2), "r": round(p.rot, 2)}
            for p in self.players.values()
        ]

game_state = GameState()
