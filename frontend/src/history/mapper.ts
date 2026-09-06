import type { ChatTurn } from "../components/ConversationThread";
import type { ChatHistoryTurn } from "./types";

export function mapHistoryToChatTurns(
  history: ChatHistoryTurn[],
  fallbackTimestamp: string,
): ChatTurn[] {
  return history.map((turn) => ({
    id: turn.id,
    content: turn.user_content,
    events: [],
    files: [],
    isRunning: false,
    result: turn.assistant_content,
    timestamp: turn.timestamp || fallbackTimestamp,
  }));
}
