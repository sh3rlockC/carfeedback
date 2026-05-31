import { redirect } from "next/navigation";

export default function LegacyVehiclePage() {
  redirect("/tasks/new");
}
