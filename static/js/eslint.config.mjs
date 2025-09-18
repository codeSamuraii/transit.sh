export default [
    {
        files: ["**/*.js"],
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "script",
            globals: {
                // Browser globals
                console: "readonly",
                window: "readonly",
                document: "readonly",
                navigator: "readonly",
                localStorage: "readonly",
                sessionStorage: "readonly",
                fetch: "readonly",
                WebSocket: "readonly",
                URL: "readonly",
                Blob: "readonly",
                File: "readonly",
                FileReader: "readonly",
                FormData: "readonly",
                URLSearchParams: "readonly",
                AbortController: "readonly",
                ArrayBuffer: "readonly",
                TextDecoder: "readonly",
                self: "readonly",
                alert: "readonly",
                setTimeout: "readonly",
                setInterval: "readonly",
                clearTimeout: "readonly",
                clearInterval: "readonly",
                Promise: "readonly",

                // Project-specific
                pdfjsLib: "readonly",
            }
        },
        rules: {
            // === Core Error Prevention ===
            // These catch real bugs and security issues
            "no-undef": "error",
            "no-unused-vars": ["error", {
                "argsIgnorePattern": "^_",
                "varsIgnorePattern": "^_"
            }],
            "no-redeclare": "error",
            "no-eval": "error",
            "no-implied-eval": "error",
            "eqeqeq": ["error", "always", { "null": "ignore" }],
            "no-return-await": "error",
            "require-await": "error",
            "no-throw-literal": "error",

            // === Consistency for Readability ===
            // Simple rules that make code more predictable
            "semi": ["error", "always"],
            "quotes": ["error", "single", { "avoidEscape": true }],
            "curly": ["error", "multi-line", "consistent"],
            "no-var": "error",
            "prefer-const": ["error", { "destructuring": "any" }],

            // === Code Clarity ===
            // Prevent confusing patterns
            "no-shadow": ["error", { "builtinGlobals": false }],
            "no-confusing-arrow": "error",
            "no-mixed-operators": ["error", {
                "groups": [
                    ["&", "|", "^", "~", "<<", ">>", ">>>"],
                    ["==", "!=", "===", "!==", ">", ">=", "<", "<="],
                    ["&&", "||"],
                    ["in", "instanceof"]
                ]
            }],

            // === Formatting ===
            // Minimal formatting for consistency
            "indent": ["error", 4, { "SwitchCase": 1 }],
            "linebreak-style": ["error", "unix"],
            "comma-dangle": ["error", "never"],
            "object-curly-spacing": ["error", "always"],
            "array-bracket-spacing": ["error", "never"],

            // === Pragmatic Defaults ===
            // Allow common patterns that make sense
            "no-console": "off",  // File transfer app needs logging
            "no-alert": "off",    // May be used for user notifications
            "no-debugger": "warn", // Warn but don't error during development
            "no-empty": ["error", { "allowEmptyCatch": true }],
            "no-unused-expressions": ["error", {
                "allowShortCircuit": true,
                "allowTernary": true
            }],

            // === Disabled Rules ===
            // These are too opinionated or conflict with "less is more"
            // - no-magic-numbers: Constants should be self-explanatory by name
            // - max-len: Let developers use judgment
            // - prefer-destructuring: Sometimes direct access is clearer
            // - no-else-return: Early returns can be clearer
            // - prefer-template: String concatenation can be simpler
            // - prefer-arrow-callback: Regular functions are fine
        }
    }
];