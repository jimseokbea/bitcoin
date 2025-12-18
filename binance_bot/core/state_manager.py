import json
import os
from .system_utils import LOGGER

class StateManager:
    def __init__(self, filename="bot_state.json"):
        self.filename = filename
        
    def load_state(self):
        """
        Loads the bot state from JSON file.
        Returns empty dict if file not found or error.
        """
        if not os.path.exists(self.filename):
            return {}
            
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                state = json.load(f)
                LOGGER.info(f"💾 상태 파일 로드 성공: {self.filename}")
                return state
        except Exception as e:
            LOGGER.error(f"상태 파일 로드 실패: {e}")
            return {}
            
    def save_state(self, state):
        """
        Saves the bot state to JSON file.
        """
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=4)
                # LOGGER.debug(f"💾 상태 저장 완료") 
        except Exception as e:
            LOGGER.error(f"상태 저장 실패: {e}")
