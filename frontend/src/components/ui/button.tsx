import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-[var(--radius-pill)] text-[13px] font-semibold transition-all duration-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-[var(--accent)] text-[var(--accent-contrast)] hover:opacity-90 hover:scale-[1.01] active:scale-[0.98]",
        destructive: "bg-red-500 text-white hover:bg-red-500/90",
        outline: "border-[0.5px] border-[var(--border-strong)] bg-transparent text-primary hover:bg-[var(--bg-hover)]",
        secondary: "border-[0.5px] border-[var(--border-default)] bg-[var(--bg-elevated)] text-secondary hover:text-primary hover:bg-[var(--bg-hover)]",
        ghost: "text-secondary hover:text-primary hover:bg-[var(--bg-hover)]",
        link: "text-[var(--accent)] underline-offset-4 hover:underline",
        pill: "bg-[var(--accent)] text-[var(--accent-contrast)] hover:opacity-90 hover:scale-[1.01] active:scale-[0.98]",
      },
      size: {
        default: "h-10 px-[22px] py-[10px]",
        sm: "h-8 px-3 text-[13px]",
        lg: "h-11 px-6 text-[15px]",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
