import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { LoginDialog } from "./LoginDialog";

describe("LoginDialog", () => {
  it("renders without loading a decorative logo image", () => {
    render(<LoginDialog open onClose={vi.fn()} onAuthenticate={vi.fn()} />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("submits fixed credentials and closes only after successful verification", async () => {
    const authenticate = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn();
    render(<LoginDialog open onClose={close} onAuthenticate={authenticate} />);

    fireEvent.change(screen.getByLabelText("账号"), { target: { value: "nova" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录并继续" }));

    await waitFor(() => expect(authenticate).toHaveBeenCalledWith("nova", "test-password"));
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("keeps the dialog open and shows the server error when verification fails", async () => {
    const authenticate = vi.fn().mockRejectedValue(new Error("账号或密码错误"));
    const close = vi.fn();
    render(<LoginDialog open onClose={close} onAuthenticate={authenticate} />);

    fireEvent.change(screen.getByLabelText("账号"), { target: { value: "nova" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "登录并继续" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码错误");
    expect(close).not.toHaveBeenCalled();
  });
});
it("shows a session-expired message when reopened after authentication expires", () => {
  render(
    <LoginDialog
      open
      expired
      onClose={vi.fn()}
      onAuthenticate={vi.fn()}
    />,
  );

  expect(screen.getByRole("alert")).toHaveTextContent(
    "登录状态已失效，请重新登录后继续使用。",
  );
});
