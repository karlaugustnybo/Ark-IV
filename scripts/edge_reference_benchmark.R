# Runs the unmodified author calculator on saved trees and probability vectors.
# Acquired author code and tree.rda side effects remain under ignored data/edge.
args <- commandArgs(trailingOnly=TRUE)
if (length(args) != 3) stop('Expected reference source, case CSV and private R library')
.libPaths(c(normalizePath(args[3]), .libPaths()))
library(ape)
library(phylobase)
library(data.table)
source(args[1])
cases <- read.csv(args[2], stringsAsFactors=FALSE)
for (i in seq_len(nrow(cases))) {
  tree <- read.tree(cases$tree[i])
  probabilities <- read.csv(cases$probabilities[i], stringsAsFactors=FALSE)
  stopifnot(!anyDuplicated(tree$tip.label), !anyDuplicated(probabilities$species))
  stopifnot(setequal(tree$tip.label, probabilities$species))
  result <- EDGE2_mod(tree, probabilities)
  write.csv(result[[1]], cases$output[i], row.names=FALSE)
  cat('Reference case', i, '/', nrow(cases), 'complete\n')
}
unlink('tree.rda')
writeLines(capture.output(sessionInfo()), 'reference_R_session.txt')
