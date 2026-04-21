import * as React from "react"
import { cn } from "@/lib/utils"

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          "flex min-h-[80px] w-full rounded-[var(--radius-md)] border-[0.5px] border-[var(--border-default)] bg-[var(--bg-surface)] px-[14px] py-[10px] text-[14px] text-primary ring-offset-background placeholder:text-[var(--text-placeholder)] focus-visible:border-[var(--accent-border)] focus-visible:outline-none focus-visible:ring-0 focus-visible:shadow-[0_0_0_3px_var(--accent-dim)] disabled:cursor-not-allowed disabled:opacity-50 transition-[border-color,box-shadow] duration-150",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Textarea.displayName = "Textarea"

export { Textarea }
