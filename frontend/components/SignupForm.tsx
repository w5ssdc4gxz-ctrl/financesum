"use client";

import React, { useState } from "react";
import { IconBrandGoogle } from "@tabler/icons-react";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useAuth } from "@/contexts/AuthContext";
import { supabase } from "@/lib/supabase";

interface SignupFormProps {
  className?: string;
}

export default function SignupForm({ className }: SignupFormProps) {
  const { signIn } = useAuth();
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [googleLoading, setGoogleLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (submitting) return;

    const form = e.currentTarget;
    const formData = new FormData(form);
    const firstName = formData.get("firstname")?.toString().trim() ?? "";
    const lastName = formData.get("lastname")?.toString().trim() ?? "";
    const email = formData.get("email")?.toString().trim() ?? "";
    const password = formData.get("password")?.toString() ?? "";

    if (!email || !password) {
      setMessage("Please enter a valid email and password.");
      return;
    }

    setSubmitting(true);
    setMessage(null);
    try {
      const envSiteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "").replace(/\/$/, "");
      const runtimeOrigin =
        typeof window !== "undefined" ? window.location.origin.replace(/\/$/, "") : "";
      const redirectBase = envSiteUrl || runtimeOrigin;
      const redirectTo = redirectBase ? `${redirectBase}/auth/callback` : undefined;
      const { error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: redirectTo,
          data: {
            first_name: firstName,
            last_name: lastName,
          },
        },
      });

      if (error) {
        throw error;
      }

      setMessage(
        "Check your email to confirm and finish creating your account.",
      );
      form.reset();
    } catch (err: any) {
      const msg =
        err?.message ||
        "We couldn’t create your account. Please try again in a moment.";
      setMessage(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleGoogleSignIn = async () => {
    try {
      setGoogleLoading(true);
      setMessage(null);
      await signIn();
    } catch (error: any) {
      setMessage(error?.message ?? "Unable to start Google sign-in right now.");
    } finally {
      setGoogleLoading(false);
    }
  };

  return (
    <div
      className={cn(
        "shadow-input mx-auto w-full max-w-md rounded-none bg-white p-5 md:rounded-3xl md:p-10 dark:bg-black",
        className,
      )}
    >
      <h2 className="text-2xl font-bold text-neutral-900 dark:text-neutral-100">
        Join FinanceSum
      </h2>
      <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">
        Start building investor-ready memos with lightning-fast SEC filing
        analysis.
      </p>

      <form className="my-8 space-y-6" onSubmit={handleSubmit}>
        <div className="flex flex-col space-y-2 md:flex-row md:space-y-0 md:space-x-3">
          <LabelInputContainer>
            <Label htmlFor="firstname">First name</Label>
            <Input
              id="firstname"
              name="firstname"
              placeholder="Avery"
              autoComplete="given-name"
              required
            />
          </LabelInputContainer>
          <LabelInputContainer>
            <Label htmlFor="lastname">Last name</Label>
            <Input
              id="lastname"
              name="lastname"
              placeholder="Jordan"
              autoComplete="family-name"
              required
            />
          </LabelInputContainer>
        </div>

        <LabelInputContainer>
          <Label htmlFor="email">Work email</Label>
          <Input
            id="email"
            name="email"
            type="email"
            placeholder="investor@fund.com"
            autoComplete="email"
            required
          />
        </LabelInputContainer>

        <LabelInputContainer>
          <Label htmlFor="password">Create password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            placeholder="••••••••"
            autoComplete="new-password"
            required
          />
        </LabelInputContainer>

        <button
          className="group/btn relative block h-12 w-full rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 font-semibold text-white shadow-[0px_1px_0px_0px_#ffffff3d_inset,0px_-1px_0px_0px_#ffffff1f_inset]"
          type="submit"
          disabled={submitting}
        >
          {submitting ? "Submitting..." : "Create account"}
          <BottomGradient />
        </button>

        <div className="h-[1px] w-full bg-gradient-to-r from-transparent via-neutral-300 to-transparent dark:via-neutral-700" />

        <button
          type="button"
          onClick={handleGoogleSignIn}
          className="group/btn shadow-input relative flex h-12 w-full items-center justify-center gap-3 rounded-xl bg-gray-50 px-4 font-medium text-black transition-colors hover:bg-white dark:bg-zinc-900 dark:text-white"
          disabled={googleLoading}
        >
          <IconBrandGoogle className="h-5 w-5 text-neutral-800 dark:text-neutral-100" />
          <span className="text-sm">
            {googleLoading ? "Connecting to Google..." : "Continue with Google"}
          </span>
          <BottomGradient />
        </button>

        {message && (
          <p className="text-center text-sm text-neutral-500 dark:text-neutral-300">
            {message}
          </p>
        )}
      </form>
    </div>
  );
}

const BottomGradient = () => {
  return (
    <>
      <span className="absolute inset-x-0 -bottom-px block h-px w-full bg-gradient-to-r from-transparent via-cyan-500 to-transparent opacity-0 transition duration-500 group-hover/btn:opacity-100" />
      <span className="absolute inset-x-10 -bottom-px mx-auto block h-px w-1/2 bg-gradient-to-r from-transparent via-indigo-500 to-transparent opacity-0 blur-sm transition duration-500 group-hover/btn:opacity-100" />
    </>
  );
};

const LabelInputContainer = ({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) => {
  return (
    <div className={cn("flex w-full flex-col space-y-2", className)}>
      {children}
    </div>
  );
};
