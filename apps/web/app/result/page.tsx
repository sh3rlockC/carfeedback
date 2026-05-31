import { redirect } from "next/navigation";

export default function LegacyResultPage() {
  redirect("/tasks");
}
