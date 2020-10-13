//Mason Brainard
//9/9/20
//section a

#define _XOPEN_SOURCE 500
#include <unistd.h>
#include <ftw.h>
#include <stdio.h>
#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>

static int display_info(const char *fpath, const struct stat *sb, int tflag, struct FTW *ftwbuf)
{
	if (ftwbuf->level > 0 || tflag == FTW_F) {
		//how many tabs to add
		for (int i = 0; i < ftwbuf->level - 1; i = i + 1) {
			printf("%c", 9);
		}

		//what file/directory/sym
		if (tflag == FTW_D || tflag == FTW_F) {
			printf("%s", fpath + ftwbuf->base);
		} else if (tflag == FTW_SL) {
			//send to readlink function which will return the path
			char *buf;
			// ssize_t nbytes, bufsiz;
			ssize_t bufsiz;

			bufsiz = 4096;

			buf = malloc(bufsiz);

			int len = readlink(fpath, buf, bufsiz);
			if (len == -1) {
				fprintf(stderr, "Error reading link: %s\n", fpath);
				return 1;
			}

			buf[len] = '\0';
			printf("SYM %s -> %s", fpath + ftwbuf->base, buf);

			free(buf);
		}
		printf("\n");
	}
	return 0;
}



int main(int argc, char *argv[])
{
	int flags = 0;
	char cwd[1024];
	//save cwd

	//if argc < 2 try to cwd	
	if(argc == 2)
	{
		if(getcwd(cwd, sizeof(cwd)) == NULL)
		{
			fprintf(stderr, "Couldn't print working directory? Sorry???\n");
			return 1;
		}
		if(chdir(argv[1]) == -1)
		{
			fprintf(stderr, "Couldn't change to specified directory: %s\n", argv[1]);
			return 1;
		}
	}
	
	chdir(argv[1]);
	flags |= FTW_PHYS;

	if (nftw(".", display_info, 20, flags) == -1) {
		fprintf(stderr, "NFTW Failed.\n");
		return 1;
	}

	if(argc == 2)
	{
		if(chdir(cwd) == -1)
		{
			fprintf(stderr, "Couldn't change to specified directory: %s\n", argv[1]);
			return 1;
		}
	}
	exit(0);
}
