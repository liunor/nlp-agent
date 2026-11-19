import { api } from "./api";

it("disables classroom membership using the server contract", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  try {
    await api.removeClassroomMember("class-1", "student-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/classrooms/class-1/members/student-1", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ member_role: "student", status: "disabled" }),
    }));
  } finally { fetchMock.mockRestore(); }
});
