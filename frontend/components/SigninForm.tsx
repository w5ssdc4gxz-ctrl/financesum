"use client";

import React, { useState } from "react";
import Link from "next/link";
import { IconBrandGoogle } from "@tabler/icons-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/contexts/AuthContext";

interface SigninFormProps {
  className?: string;
}

export default function SigninForm({ className }: SigninFormProps) {
  const { signIn } = useAuth();
  const [message, setMessage] = useState<string | null>(null);
  const [googleLoading, setGoogleLoading] = useState(false);

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
        Welcome back
      </h2>
      <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">
        Continue with Google to sign in.
      </p>

      <div className="my-8 space-y-6">
        <button
          type="button"
          onClick={handleGoogleSignIn}
          className="group/btn shadow-input relative flex h-12 w-full items-center justify-center gap-3 rounded-xl border border-neutral-200 bg-white px-4 font-medium text-black transition-colors hover:bg-neutral-50 dark:border-white/10 dark:bg-zinc-900 dark:text-white dark:hover:bg-zinc-800"
          disabled={googleLoading}
        >
          <IconBrandGoogle className="h-5 w-5 text-neutral-800 dark:text-neutral-100" />
          <span className="text-sm">
            {googleLoading ? "Connecting to Google..." : "Continue with Google"}
          </span>
          <BottomGradient />
        </button>

        <p className="text-center text-sm text-neutral-500 dark:text-neutral-300">
          Don&apos;t have an account yet?{" "}
          <Link href="/signup" className="font-semibold text-neutral-900 dark:text-white">
            Sign up
          </Link>
        </p>

        {message && (
          <p className="text-center text-sm text-neutral-500 dark:text-neutral-300">
            {message}
          </p>
        )}
      </div>
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
