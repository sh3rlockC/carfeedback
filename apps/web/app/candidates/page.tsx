import { redirect } from "next/navigation";

export default function LegacyCandidatesPage() {
  redirect("/tasks/new");
}
