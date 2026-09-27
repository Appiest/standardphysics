import type { ReviewQueue } from "@/types/contracts";
import { loadTeam } from "./loadTeam";

export const loadReviewQueue = () => loadTeam<ReviewQueue>("/api/team/reviews");
